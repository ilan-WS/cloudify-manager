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

from flask import request
from flask_restful_swagger import swagger

from manager_rest.security.authorization import authorize
from manager_rest.storage import get_storage_manager, models
from manager_rest.utils import create_filter_params_list_description
from manager_rest.rest.filters_utils import get_filter_rules_from_filter_id
from manager_rest.rest import (resources_v1,
                               rest_decorators,
                               rest_utils)


class Blueprints(resources_v1.Blueprints):
    @swagger.operation(
        responseClass='List[{0}]'.format(models.Blueprint.__name__),
        nickname="list",
        notes='Returns a list of submitted blueprints for the optionally '
              'provided filter parameters {0}'
        .format(models.Blueprint),
        parameters=create_filter_params_list_description(
            models.Blueprint.response_fields,
            'blueprints'
        )
    )
    @authorize('blueprint_list', allow_all_tenants=True)
    @rest_decorators.marshal_with(models.Blueprint)
    @rest_decorators.create_filters(models.Blueprint)
    @rest_decorators.paginate
    @rest_decorators.sortable(models.Blueprint)
    @rest_decorators.all_tenants
    @rest_decorators.search('id')
    @rest_decorators.filter_id
    def get(self, _include=None, filters=None, pagination=None, sort=None,
            all_tenants=None, search=None, filter_id=None, **kwargs):
        """
        List uploaded blueprints
        """
        get_all_results = rest_utils.verify_and_convert_bool(
            '_get_all_results',
            request.args.get('_get_all_results', False)
        )
        filters, _include = rest_utils.modify_blueprints_list_args(filters,
                                                                   _include)
        filter_rules = get_filter_rules_from_filter_id(filter_id,
                                                       models.BlueprintsFilter)
        return get_storage_manager().list(
            models.Blueprint,
            include=_include,
            filters=filters,
            substr_filters=search,
            pagination=pagination,
            sort=sort,
            all_tenants=all_tenants,
            get_all_results=get_all_results,
            filter_rules=filter_rules
        )


class BlueprintsId(resources_v1.BlueprintsId):

    @swagger.operation(
        responseClass=models.Blueprint,
        nickname="getById",
        notes="Returns a blueprint by its id."
    )
    @authorize('blueprint_get')
    @rest_decorators.marshal_with(models.Blueprint)
    def get(self, blueprint_id, _include=None, **kwargs):
        """
        Get blueprint by id
        """
        if _include and 'labels' in _include:
            _include = None
        with rest_utils.skip_nested_marshalling():
            return super(BlueprintsId, self).get(blueprint_id=blueprint_id,
                                                 _include=_include,
                                                 **kwargs)

    @swagger.operation(
        responseClass=models.Blueprint,
        nickname="upload",
        notes="Submitted blueprint should be an archive "
              "containing the directory which contains the blueprint. "
              "Archive format may be zip, tar, tar.gz or tar.bz2."
              " Blueprint archive may be submitted via either URL or by "
              "direct upload.",
        parameters=[{'name': 'application_file_name',
                     'description': 'File name of yaml '
                                    'containing the "main" blueprint.',
                     'required': False,
                     'allowMultiple': False,
                     'dataType': 'string',
                     'paramType': 'query',
                     'defaultValue': 'blueprint.yaml'},
                    {'name': 'blueprint_archive_url',
                     'description': 'url of a blueprint archive file',
                     'required': False,
                     'allowMultiple': False,
                     'dataType': 'string',
                     'paramType': 'query'},
                    {
                        'name': 'body',
                        'description': 'Binary form of the tar '
                                       'gzipped blueprint directory',
                        'required': True,
                        'allowMultiple': False,
                        'dataType': 'binary',
                        'paramType': 'body'}],
        consumes=[
            "application/octet-stream"
        ]

    )
    @authorize('blueprint_upload')
    @rest_decorators.marshal_with(models.Blueprint)
    def put(self, blueprint_id, **kwargs):
        """
        Upload a blueprint (id specified)
        """
        with rest_utils.skip_nested_marshalling():
            return super(BlueprintsId, self).put(blueprint_id=blueprint_id,
                                                 **kwargs)

    @swagger.operation(
        responseClass=models.Blueprint,
        nickname="deleteById",
        notes="deletes a blueprint by its id."
    )
    @authorize('blueprint_delete')
    @rest_decorators.marshal_with(models.Blueprint)
    def delete(self, blueprint_id, **kwargs):
        """
        Delete blueprint by id
        """
        with rest_utils.skip_nested_marshalling():
            return super(BlueprintsId, self).delete(
                blueprint_id=blueprint_id, **kwargs)
