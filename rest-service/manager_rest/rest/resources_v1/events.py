#########
# Copyright (c) 2017 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.
#

from functools import reduce

from sqlalchemy import (
    asc,
    bindparam,
    desc,
    literal_column,
    or_ as sql_or
)
from cloudify.models_states import VisibilityState

from manager_rest import manager_exceptions, utils
from manager_rest.rest.rest_decorators import insecure_rest_method
from manager_rest.security import SecuredResource
from manager_rest.security.authorization import authorize
from manager_rest.storage.models_base import db
from manager_rest.storage.resource_models import (
    Blueprint,
    Deployment,
    Execution,
    Event,
    ExecutionGroup,
    Log,
    Node,
    NodeInstance,
)


class Events(SecuredResource):

    """Events resource.

    Through the events endpoint a user can retrieve both events and logs as
    stored in the SQL database.

    """

    DEFAULT_SEARCH_SIZE = 10000

    # <filter name (passed as rest param)>: (<column name>, <comparison>)
    ALLOWED_FILTERS = {
        'node_id': (Node.id, 'in'),
        'node_instance_id': (NodeInstance.id, 'in'),
        'operation': ('operation', 'ilike'),
        'blueprint_id': (Blueprint.id, 'in'),
        'execution_id': (Execution.id, 'in'),
        'execution_group_id': ('execution_group_id', 'in'),
        'deployment_id': (Deployment.id, 'in'),
        'event_type': (Event.event_type, 'in'),
        'level': (Log.level, 'in'),
        'message': ('message', 'ilike'),
    }

    # Map from old Elasticsearch field name to PostgreSQL one
    ES_TO_PG_FILTER_FIELD = {
        'message.text': 'message',
    }

    @staticmethod
    def _apply_filters(query, model, filters):
        """Apply filters to the query.

        :param query: Base query to update with filters
        :type query: :class:`sqlalchemy.orm.query.Query`
        :param model: Model used to filter by default
        :type model:
            :class:`manager_rest.storage.resource_models.Event`
            :class:`manager_rest.storage.resource_models.Log`
        :param filters:
            Dictionary of filters where the key is the column to filter and the
            value is a list of elements that can be matched using the `IN`
            operator.
        :type filters: dict(str, list(str))

        """
        for filter_field, filter_ in filters.items():
            filter_field = Events.ES_TO_PG_FILTER_FIELD.get(
                filter_field, filter_field)

            if filter_field == 'type':
                # Filter by type is handled while building the query
                continue
            if filter_field not in Events.ALLOWED_FILTERS:
                raise manager_exceptions.BadParametersError(
                    'Unknown field to filter by: {0}. '
                    'Allowed values: {1}'
                    .format(
                        filter_field,
                        ', '.join(sorted(Events.ALLOWED_FILTERS.keys())),
                    ))
            model_field, filter_type = Events.ALLOWED_FILTERS[filter_field]
            if isinstance(model_field, str):
                model_field = getattr(model, model_field)

            if filter_type == 'in':
                query = query.filter(model_field.in_(filter_))
            elif filter_type == 'ilike':
                for filter_element in filter_:
                    query = query.filter(model_field.ilike(filter_element))
            else:
                raise ValueError(
                    'Unknown filter type: {0}. '
                    'Allowed values: ilike, in'
                    .format(filter_type)
                )

        return query

    @staticmethod
    def _apply_sort(query, sort):
        """Apply sorting criteria.

        Sorting will be rejected if the field doesn't match any of the column
        names that has been selected for the query. Note that the query
        involves two models at the same time, it is not possible to just check
        a model.

        :param query: Query in which the sorting should be applied
        :type query: :class:`sqlalchemy.orm.query.Query`
        :param sort: Sorting criteria passed as a request argument
        :type sort: dict(str, str)
        :returns: Query with sorting criteria applied
        :rtype: :class:`sqlalchemy.orm.query.Query`

        """
        column_names = set(
            column_description['name']
            for column_description in query.column_descriptions
        )
        for field, order in sort.items():
            # Drop `@` prefix for compatibility
            # with old Elasticsearch based implementation
            field = field.lstrip('@')
            if field not in column_names:
                raise manager_exceptions.BadParametersError(
                    'Unknown field to sort by: {}'.format(field))

            order_func = asc if order == 'asc' else desc
            query = query.order_by(order_func(field))
        return query

    @staticmethod
    def _apply_range_filters(query, model, range_filters):
        """Apply range filters to query.

        :param query: Query in which the filtering should be applied
        :type query: :class:`sqlalchemy.orm.query.Query`
        :param model: Model to use to apply the filtering
        :type model:
            :class:`manager_rest.storage.resource_models.Event`
            :class:`manager_rest.storage.resource_models.Log`
        :param range_filters: Range filters passed as a request argument
        :type range_filters: dict(str, dict(str))
        :returns: Query with filtering applied
        :rtype: :class:`sqlalchemy.orm.query.Query`

        """
        for field, range_filter in range_filters.items():
            # Drop `@` prefix for compatibility
            # with old Elasticsearch based implementation
            field = field.lstrip('@')
            if not hasattr(model, field):
                raise manager_exceptions.BadParametersError(
                    'Unknown field to filter by range: {}'.format(field))
            query = Events._apply_range_filter(
                query, model, field, range_filter)
        return query

    @staticmethod
    def _apply_range_filter(query, model, field, range_filter):
        """Apply a range filter to query.

        :param query: Query in which the filtering should be applied
        :type query: :class:`sqlalchemy.orm.query.Query`
        :param model: Model to use to apply the filtering
        :type model:
            :class:`manager_rest.storage.resource_models.Event`
            :class:`manager_rest.storage.resource_models.Log`
        :param field: Field in the model that should be filtered
        :type field: str
        :param range_filter: Range filter passed as a request argument
        :type range_filter: dict(str)
        :returns: Query with filtering applied
        :rtype: :class:`sqlalchemy.orm.query.Query`

        """
        if 'from' in range_filter:
            query = query.filter(getattr(model, field) >= range_filter['from'])
        if 'to' in range_filter:
            query = query.filter(getattr(model, field) <= range_filter['to'])
        return query

    @staticmethod
    def _build_select_query(filters, sort, range_filters, tenant_id):
        """Build query used to list events for a given execution.

        :param filters:
            Filters selection.

            Valid filtering criteria are:
                - Type (return events or both events and logs):
                    {'type': ['cloudify_event', 'cloudify_log']}
                - Execution:
                    {'execution_id': <some_id>}
                - Deployment:
                    {'deployment_id': <some_id>}

            Results must match every the filtering criteria. In particular,
            filtering by a deployment and an execution that doesn't belong to
            that deployment won't return any result.
        :type filters: dict(str, str)
        :param sort:
            Result sorting order.

            The only field that is supported for now is @timestamp (note the
            `@` inherited from the old Elasticsearch implementation):
                {'timestamp': 'asc'}
        :type sort: dict(str, str)
        :param range_filters:
            Filter out events that don't fall in a given range.

            The only field that is supported for now is @timestamp (note the
            `@` inherited from the old Elasticsearch implementation):
                {'timestamp': {'from': <iso8601-date>, 'to': <iso8601-date>}}
        :type range_filters: dict(str, str)
        :returns:
            A SQL query that returns the events found that match the conditions
            passed as arguments.
        :rtype: :class:`sqlalchemy.orm.query.Query`

        """
        assert isinstance(filters, dict), \
            'Filters is expected to be a dictionary'

        subqueries = []
        if (('type' not in filters or 'cloudify_event' in filters['type']) and
                ('level' not in filters)):
            events_query = Events._build_select_subquery(
                Event, filters, range_filters, tenant_id)
            subqueries.append(events_query)

        if (('type' not in filters or 'cloudify_log' in filters['type']) and
                ('event_type' not in filters)):
            logs_query = Events._build_select_subquery(
                Log, filters, range_filters, tenant_id)
            subqueries.append(logs_query)

        if subqueries:
            query = reduce(
                lambda left, right: left.union_all(right),
                subqueries,
            )
            total = query.count()
            query = Events._apply_sort(query, sort)
            if sort:
                _, sort_direction = dict(sort).popitem()
            else:
                sort_direction = 'asc'
            query = Events._apply_sort(query, {'_storage_id': sort_direction})
            query = (
                query
                .limit(bindparam('limit'))
                .offset(bindparam('offset'))
            )
        else:
            # Simple query that returns no results
            # Used when filtering by a field that doesn't exist for a type
            query = (
                db.session.query(Event.timestamp)
                .filter(Event.timestamp is None)
            )
            total = query.count()

        return query, total

    @staticmethod
    def _build_select_subquery(model, filters, range_filters, tenant_id):
        """Build select subquery.

        :param model: Model used to build the query (either Event or Log)
        :type model:
            :class:`manager_rest.storage.resource_models.Event`
            :class:`manager_rest.storage.resource_models.Log`
        :param filters: Filters passed as request argument
        :type filters: dict(str, list(str))
        :param range_filters: Range filtres passed as request argument
        :type range_filters: dict(str, dict(str))
        :returns: Select events query
        :rtype: :class:`sqlalchemy.orm.query.Query`

        """
        def select_column(column_name, label=None):
            """Select column from model by name.

            If column is not present in the model, then select `NULL` value
            instead.

            :param column_name: Name of the column to select
            :type column_name: str
            :return: Selected colum
            :rtype: :class:``

            """
            if not label:
                label = column_name
            if hasattr(model, column_name):
                return getattr(model, column_name).label(label)
            return literal_column('NULL').label(label)

        query = (
            db.session.query(
                select_column('_storage_id'),
                select_column('timestamp'),
                select_column('reported_timestamp'),
                Blueprint.id.label('blueprint_id'),
                Deployment.id.label('deployment_id'),
                Execution.id.label('execution_id'),
                ExecutionGroup.id.label('execution_group_id'),
                Execution.workflow_id.label('workflow_id'),
                select_column('message'),
                select_column('message_code'),
                select_column('error_causes'),
                select_column('event_type'),
                select_column('operation'),
                select_column('node_id'),
                select_column('source_id'),
                select_column('target_id'),
                NodeInstance.id.label('node_instance_id'),
                Node.id.label('node_name'),
                select_column('logger'),
                select_column('level'),
                literal_column("'cloudify_{}'".format(model.__name__.lower()))
                .label('type'),
            )
            .filter(
                sql_or(
                    model._tenant_id == tenant_id,
                    model.visibility == VisibilityState.GLOBAL
                )
            )
            .outerjoin(NodeInstance, NodeInstance.id == model.node_id)
            .outerjoin(Node, Node._storage_id == NodeInstance._node_fk)
            .outerjoin(Execution, Execution._storage_id == model._execution_fk)
            .outerjoin(ExecutionGroup,
                       ExecutionGroup._storage_id == model._execution_group_fk)
            .outerjoin(Deployment,
                       Deployment._storage_id == Execution._deployment_fk)
            .outerjoin(
                Blueprint, Blueprint._storage_id == Deployment._blueprint_fk)
        )

        query = Events._apply_filters(query, model, filters)
        query = Events._apply_range_filters(query, model, range_filters)
        return query

    @staticmethod
    def _map_event_to_dict(_include, sql_event):
        """Map event to a dictionary to be sent as an API response.

        In this implementation, the goal is to restructure event data as if it
        was returned by elasticsearch. This restructuration is needed because
        the API in the past used elasticsearch as the backend and the client
        implementation still expects data that has the same shape as
        elasticsearch would return.

        :param _include:
            Projection used to get records from database
        :type _include: list(str)
        :param sql_event: Event data returned when SQL query was executed
        :type sql_event: :class:`sqlalchemy.util._collections.result`
        :returns: Event as would have returned by elasticsearch
        :rtype: dict(str)

        """
        event = {
            attr: getattr(sql_event, attr)
            for attr in sql_event.keys()
        }
        event['@timestamp'] = event['timestamp']
        del event['reported_timestamp']

        event['message'] = {
            'text': event['message']
        }

        if 'node_instance_id' in event:
            del event['node_instance_id']

        context_fields = [
            'deployment_id',
            'execution_id',
            'workflow_id',
            'operation',
            'node_id',
            'node_name',
        ]
        event['context'] = {
            field: event[field]
            for field in context_fields
        }
        for field in context_fields:
            del event[field]

        if event['type'] == 'cloudify_event':
            event['message']['arguments'] = None
            del event['logger']
            del event['level']
        elif event['type'] == 'cloudify_log':
            del event['event_type']

        # Keep only keys passed in the _include request argument
        # TBD: Do the projection at the database level
        if _include is not None:
            event = {k: v for k, v in event.items() if k in _include}

        return event

    @authorize('event_list')
    @insecure_rest_method
    def get(self, **kwargs):
        raise manager_exceptions.MethodNotAllowedError()

    @authorize('event_list')
    @insecure_rest_method
    def post(self, **kwargs):
        raise manager_exceptions.MethodNotAllowedError()

    @property
    def current_tenant(self):
        """Return the tenant with which the user accessed the app
        """
        return utils.current_tenant
