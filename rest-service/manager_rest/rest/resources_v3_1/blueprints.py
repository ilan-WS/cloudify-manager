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

from os.path import join

from flask import request

from flask_restful.inputs import boolean
from flask_restful.reqparse import Argument
from flask_restful_swagger import swagger

from cloudify._compat import text_type
from cloudify.models_states import VisibilityState, BlueprintUploadState

from dsl_parser import constants

from manager_rest.security import SecuredResource
from manager_rest.security.authorization import authorize
from manager_rest.resource_manager import get_resource_manager
from manager_rest.upload_manager import (UploadedBlueprintsManager,
                                         UploadedBlueprintsValidator)
from manager_rest.rest import (rest_utils,
                               resources_v1,
                               resources_v2,
                               rest_decorators)
from manager_rest.storage import models, get_storage_manager
from manager_rest.utils import get_formatted_timestamp, remove
from manager_rest.rest.rest_utils import (get_labels_from_plan,
                                          get_args_and_verify_arguments)
from manager_rest.manager_exceptions import (ConflictError,
                                             IllegalActionError,
                                             BadParametersError,
                                             DeploymentParentNotFound,)
from manager_rest import config
from manager_rest.constants import FILE_SERVER_UPLOADED_BLUEPRINTS_FOLDER


class BlueprintsSetGlobal(SecuredResource):

    @authorize('resource_set_global')
    @rest_decorators.marshal_with(models.Blueprint)
    def patch(self, blueprint_id):
        """
        Set the blueprint's visibility to global
        """
        return get_resource_manager().set_visibility(
            models.Blueprint,
            blueprint_id,
            VisibilityState.GLOBAL
        )


class BlueprintsSetVisibility(SecuredResource):

    @authorize('resource_set_visibility')
    @rest_decorators.marshal_with(models.Blueprint)
    def patch(self, blueprint_id):
        """
        Set the blueprint's visibility
        """
        visibility = rest_utils.get_visibility_parameter()
        return get_resource_manager().set_visibility(models.Blueprint,
                                                     blueprint_id,
                                                     visibility)


class BlueprintsId(resources_v2.BlueprintsId):
    @authorize('blueprint_upload')
    @rest_decorators.marshal_with(models.Blueprint)
    def put(self, blueprint_id, **kwargs):
        """
        Upload a blueprint (id specified)
        """
        rest_utils.validate_inputs({'blueprint_id': blueprint_id})
        args = get_args_and_verify_arguments(
            [Argument('async_upload', type=boolean, default=False),
             Argument('labels')]
        )
        async_upload = args.async_upload
        visibility = rest_utils.get_visibility_parameter(
            optional=True,
            is_argument=True,
            valid_values=VisibilityState.STATES
        )
        labels = self._get_labels_from_args(args)
        # Fail fast if trying to upload a duplicate blueprint.
        # Allow overriding an existing blueprint which failed to upload
        current_tenant = request.headers.get('tenant')
        override_failed = False

        if visibility == VisibilityState.GLOBAL:
            existing_duplicates = get_storage_manager().list(
                models.Blueprint, filters={'id': blueprint_id})
            if existing_duplicates:
                if existing_duplicates[0].state in \
                        BlueprintUploadState.FAILED_STATES:
                    override_failed = True
                else:
                    raise IllegalActionError(
                        "Can't set or create the resource `{0}`, it's "
                        "visibility can't be global because it also exists in "
                        "other tenants".format(blueprint_id))
        else:
            existing_duplicates = get_storage_manager().list(
                models.Blueprint, filters={'id': blueprint_id,
                                           'tenant_name': current_tenant})
            if existing_duplicates:
                if existing_duplicates[0].state in \
                        BlueprintUploadState.FAILED_STATES:
                    override_failed = True
                else:
                    raise ConflictError(
                        'blueprint with id={0} already exists on tenant {1} '
                        'or with global visibility'.format(blueprint_id,
                                                           current_tenant))
        response = UploadedBlueprintsManager().\
            receive_uploaded_data(data_id=blueprint_id,
                                  visibility=visibility,
                                  override_failed=override_failed,
                                  labels=labels)
        if not async_upload:
            sm = get_storage_manager()
            blueprint, _ = response
            response = rest_utils.get_uploaded_blueprint(sm, blueprint)
        return response

    @staticmethod
    def _get_labels_from_args(args):
        if args.labels:
            labels_list = []
            raw_labels_list = args.labels.replace('\\,', '\x00').split(',')
            for raw_label in raw_labels_list:
                raw_label = raw_label.replace('\x00', ',').\
                    replace('\\=', '\x00')
                key, value = raw_label.split('=')
                key = key.replace('\x00', '=')
                value = value.replace('\x00', '=')
                labels_list.append({key: value})
            return rest_utils.get_labels_list(labels_list)

        return None

    @swagger.operation(
        responseClass=models.Blueprint,
        nickname="deleteById",
        notes="deletes a blueprint by its id."
    )
    @authorize('blueprint_delete')
    def delete(self, blueprint_id, **kwargs):
        """
        Delete blueprint by id
        """
        query_args = get_args_and_verify_arguments(
            [Argument('force', type=boolean, default=False)])
        get_resource_manager().delete_blueprint(
            blueprint_id,
            force=query_args.force)
        return None, 204

    @authorize('blueprint_upload')
    @rest_decorators.marshal_with(models.Blueprint)
    def patch(self, blueprint_id, **kwargs):
        """
        Update a blueprint.

        Used for updating the blueprint's state (and error) while uploading,
        and updating the blueprint's other attributes upon a successful upload.
        This method is for internal use only.
        """
        if not request.json:
            raise IllegalActionError('Update a blueprint request must include '
                                     'at least one parameter to update')

        request_schema = {
            'plan': {'type': dict, 'optional': True},
            'description': {'type': text_type, 'optional': True},
            'main_file_name': {'type': text_type, 'optional': True},
            'visibility': {'type': text_type, 'optional': True},
            'state': {'type': text_type, 'optional': True},
            'error': {'type': text_type, 'optional': True},
            'error_traceback': {'type': text_type, 'optional': True},
            'labels': {'type': list, 'optional': True}
        }
        request_dict = rest_utils.get_json_and_verify_params(request_schema)

        invalid_params = set(request_dict.keys()) - set(request_schema.keys())
        if invalid_params:
            raise BadParametersError(
                "Unknown parameters: {}".format(','.join(invalid_params))
            )
        sm = get_storage_manager()
        rm = get_resource_manager()
        blueprint = sm.get(models.Blueprint, blueprint_id)

        # if finished blueprint validation - cleanup DB entry
        # and uploaded blueprints folder
        if blueprint.state == BlueprintUploadState.VALIDATING:
            uploaded_blueprint_path = join(
                config.instance.file_server_root,
                FILE_SERVER_UPLOADED_BLUEPRINTS_FOLDER,
                blueprint.tenant.name,
                blueprint.id)
            remove(uploaded_blueprint_path)
            sm.delete(blueprint)
            return blueprint

        # set blueprint visibility
        visibility = request_dict.get('visibility')
        if visibility:
            if visibility not in VisibilityState.STATES:
                raise BadParametersError(
                    "Invalid visibility: `{0}`. Valid visibility's values "
                    "are: {1}".format(visibility, VisibilityState.STATES)
                )
            blueprint.visibility = visibility

        # set other blueprint attributes.
        if 'plan' in request_dict:
            blueprint.plan = request_dict['plan']
        if 'description' in request_dict:
            blueprint.description = request_dict['description']
        if 'main_file_name' in request_dict:
            blueprint.main_file_name = request_dict['main_file_name']
        provided_labels = request_dict.get('labels')

        if request_dict.get('plan'):
            dsl_labels = request_dict['plan'].get('labels', {})
            csys_obj_parents = dsl_labels.get('csys-obj-parent')
            if csys_obj_parents:
                dep_parents = csys_obj_parents['values']
                missing_parents = rm.get_missing_deployment_parents(
                    dep_parents
                )
                if missing_parents:
                    raise DeploymentParentNotFound(
                        'Blueprint {0}: is referencing deployments'
                        ' using label `csys-obj-parent` that does not exist, '
                        'make sure that deployment(s) {1} exist before '
                        'creating blueprint'.format(
                            blueprint.id, ','.join(missing_parents)
                        )
                    )
        # set blueprint state
        state = request_dict.get('state')
        if state:
            if state not in BlueprintUploadState.STATES:
                raise BadParametersError(
                    "Invalid state: `{0}`. Valid blueprint state values are: "
                    "{1}".format(state, BlueprintUploadState.STATES)
                )
            if (state != BlueprintUploadState.UPLOADED and
                    provided_labels is not None):
                raise ConflictError(
                    'Blueprint labels can be created only if the provided '
                    'blueprint state is {0}'.format(
                        BlueprintUploadState.UPLOADED))

            blueprint.state = state
            blueprint.error = request_dict.get('error')
            blueprint.error_traceback = request_dict.get('error_traceback')

            # On finalizing the blueprint upload, extract archive to file
            # server
            if state == BlueprintUploadState.UPLOADED:
                UploadedBlueprintsManager(). \
                    extract_blueprint_archive_to_file_server(
                        blueprint_id=blueprint_id,
                        tenant=blueprint.tenant.name)
                _create_blueprint_labels(blueprint, provided_labels)

            # If failed for any reason, cleanup the blueprint archive from
            # server
            elif state in BlueprintUploadState.FAILED_STATES:
                UploadedBlueprintsManager(). \
                    cleanup_blueprint_archive_from_file_server(
                        blueprint_id=blueprint_id,
                        tenant=blueprint.tenant.name)
        else:  # Updating the blueprint not as part of the upload process
            if provided_labels is not None:
                if blueprint.state != BlueprintUploadState.UPLOADED:
                    raise ConflictError(
                        'Blueprint labels can only be updated if the blueprint'
                        ' was uploaded successfully')

                rm = get_resource_manager()
                labels_list = rest_utils.get_labels_list(provided_labels)
                rm.update_resource_labels(models.BlueprintLabel,
                                          blueprint,
                                          labels_list)

        blueprint.updated_at = get_formatted_timestamp()
        return sm.update(blueprint)


def _create_blueprint_labels(blueprint, provided_labels):
    labels_list = get_labels_from_plan(blueprint.plan,
                                       constants.BLUEPRINT_LABELS)
    if provided_labels:
        labels_list.extend(tuple(label) for label in provided_labels if
                           tuple(label) not in labels_list)
    rm = get_resource_manager()
    rm.create_resource_labels(models.BlueprintLabel, blueprint, labels_list)


class BlueprintsIdValidate(BlueprintsId):
    @authorize('blueprint_upload')
    def put(self, blueprint_id, **kwargs):
        """
        Validate a blueprint (id specified)
        """
        rest_utils.validate_inputs({'blueprint_id': blueprint_id})
        visibility = rest_utils.get_visibility_parameter(
            optional=True,
            is_argument=True,
            valid_values=VisibilityState.STATES
        )
        return UploadedBlueprintsValidator().\
            receive_uploaded_data(data_id=blueprint_id,
                                  visibility=visibility)


class BlueprintsIdArchive(resources_v1.BlueprintsIdArchive):
    @authorize('blueprint_upload')
    def put(self, blueprint_id, **kwargs):
        """
        Upload an archive for an existing a blueprint.

        Used for uploading the blueprint's archive, downloaded from a URL using
        a system workflow, to the manager's file server
        This method is for internal use only.
        """
        UploadedBlueprintsManager(). \
            upload_archive_to_file_server(blueprint_id=blueprint_id)
