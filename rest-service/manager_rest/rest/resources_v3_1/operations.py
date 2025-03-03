#########
# Copyright (c) 2018 Cloudify Platform Ltd. All rights reserved
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

from flask_restful.reqparse import Argument

from cloudify._compat import text_type
from cloudify.constants import TERMINATED_STATES

from manager_rest.rest.rest_utils import (
    get_args_and_verify_arguments,
    get_json_and_verify_params,
)
from manager_rest.rest.rest_decorators import (
    marshal_with,
    paginate
)
from manager_rest.storage import (
    get_storage_manager,
    models,
    db
)
from manager_rest.security.authorization import authorize
from manager_rest.resource_manager import get_resource_manager
from manager_rest.security import SecuredResource


class Operations(SecuredResource):
    @authorize('operations')
    @marshal_with(models.Operation)
    @paginate
    def get(self, _include=None, pagination=None, **kwargs):
        args = get_args_and_verify_arguments([
            Argument('graph_id', type=text_type, required=True)
        ])
        sm = get_storage_manager()
        graph_id = args.get('graph_id')
        tasks_graph = sm.list(models.TasksGraph, filters={'id': graph_id})[0]
        return sm.list(
            models.Operation,
            filters={'tasks_graph': tasks_graph},
            pagination=pagination,
            include=_include
        )


class OperationsId(SecuredResource):
    @authorize('operations')
    @marshal_with(models.Operation)
    def get(self, operation_id, **kwargs):
        return get_storage_manager().get(models.Operation, operation_id)

    @authorize('operations')
    @marshal_with(models.Operation)
    def put(self, operation_id, **kwargs):
        params = get_json_and_verify_params({
            'name': {'type': text_type, 'required': True},
            'graph_id': {'type': text_type, 'required': True},
            'dependencies': {'type': list, 'required': True},
            'parameters': {'type': dict},
            'type': {'type': text_type}
        })
        operation = get_resource_manager().create_operation(
            operation_id,
            name=params['name'],
            graph_id=params['graph_id'],
            dependencies=params['dependencies'],
            type=params['type'],
            parameters=params['parameters']
        )
        return operation, 201

    @authorize('operations')
    @marshal_with(models.Operation)
    def patch(self, operation_id, **kwargs):
        request_dict = get_json_and_verify_params(
            {'state': {'type': text_type}}
        )
        sm = get_storage_manager()
        with sm.transaction():
            instance = sm.get(models.Operation, operation_id, locking=True)
            old_state = instance.state
            instance.state = request_dict.get('state', instance.state)
            if not instance.is_nop and \
                    old_state not in TERMINATED_STATES and \
                    instance.state in TERMINATED_STATES:
                self._modify_execution_operations_counts(instance, 1)
            instance = sm.update(instance, modified_attrs=('state',))
        return instance

    def _modify_execution_operations_counts(self, operation, finished_delta,
                                            total_delta=0):
        """Increase finished_operations for this operation's execution

        This is a separate sql-level update query, rather than ORM-level
        calls, for performance: the operation state-update call is on
        the critical path for all operations in a workflow; this saves
        about 3ms over the ORM approach (which requires fetching the
        execution; more if the DB is not local).
        """
        exc_table = models.Execution.__table__
        tg_table = models.TasksGraph.__table__
        values = {}
        if finished_delta:
            values['finished_operations'] =\
                exc_table.c.finished_operations + finished_delta
        if total_delta:
            values['total_operations'] =\
                exc_table.c.total_operations + total_delta
        db.session.execute(
            exc_table.update()
            .where(db.and_(
                tg_table.c._execution_fk == exc_table.c._storage_id,
                tg_table.c._storage_id == operation._tasks_graph_fk,
            ))
            .values(**values)
        )

    @authorize('operations')
    @marshal_with(models.Operation)
    def delete(self, operation_id, **kwargs):
        sm = get_storage_manager()
        with sm.transaction():
            instance = sm.get(models.Operation, operation_id, locking=True)
            if not instance.is_nop:
                finished_delta = \
                    -1 if instance.state in TERMINATED_STATES else 0
                self._modify_execution_operations_counts(
                    instance, finished_delta, total_delta=-1)
            sm.delete(instance)
        return instance, 200


class TasksGraphs(SecuredResource):
    @authorize('operations')
    @marshal_with(models.TasksGraph)
    @paginate
    def get(self, _include=None, pagination=None, **kwargs):
        args = get_args_and_verify_arguments([
            Argument('execution_id', type=text_type, required=True),
            Argument('name', type=text_type, required=True)
        ])
        sm = get_storage_manager()
        execution_id = args.get('execution_id')
        name = args.get('name')
        execution = sm.list(models.Execution, filters={'id': execution_id})[0]
        return sm.list(
            models.TasksGraph,
            filters={'execution': execution, 'name': name},
            pagination=pagination
        )


class TasksGraphsId(SecuredResource):
    @authorize('operations')
    @marshal_with(models.TasksGraph)
    def post(self, **kwargs):
        params = get_json_and_verify_params({
            'name': {'type': text_type, 'required': True},
            'execution_id': {'type': text_type, 'required': True},
            'operations': {'required': False}
        })
        sm = get_storage_manager()
        with sm.transaction():
            tasks_graph = get_resource_manager().create_tasks_graph(
                name=params['name'],
                execution_id=params['execution_id'],
                operations=params.get('operations', [])
            )
        return tasks_graph, 201

    @authorize('operations')
    @marshal_with(models.TasksGraph)
    def patch(self, tasks_graph_id, **kwargs):
        request_dict = get_json_and_verify_params(
            {'state': {'type': text_type}}
        )
        sm = get_storage_manager()
        instance = sm.get(models.TasksGraph, tasks_graph_id, locking=True)
        instance.state = request_dict.get('state', instance.state)
        return sm.update(instance)
