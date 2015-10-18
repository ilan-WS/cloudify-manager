#########
# Copyright (c) 2013 GigaSpaces Technologies Ltd. All rights reserved
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

import StringIO
import functools
import traceback
import os
import yaml
from logging.handlers import RotatingFileHandler

from flask import (
    Flask,
    jsonify,
    request,
    current_app
)
from flask_restful import Api

from flask_securest.rest_security import SecuREST

from manager_rest import endpoint_mapper
from manager_rest import config
from manager_rest import storage_manager
from manager_rest import blueprints_manager
from manager_rest import manager_exceptions
from manager_rest import utils


SECURITY_BYPASS_PORT = '8101'


# app factory
def setup_app():
    app = Flask(__name__)
    cfy_config = config.instance()

    app.logger_name = 'manager-rest'
    # setting up the app logger with a rotating file handler, in addition to
    #  the built-in flask logger which can be helpful in debug mode.
    create_logger(logger_name=app.logger.name,
                  log_level=cfy_config.rest_service_log_level,
                  log_file=cfy_config.rest_service_log_path,
                  log_file_size_MB=cfy_config.rest_service_log_file_size_MB,
                  log_files_backup_count=cfy_config.
                  rest_service_log_files_backup_count)

    # secure the app according to manager configuration
    if cfy_config.security_enabled:
        app.logger.info('initializing rest-service security')
        init_secured_app(app)

    app.before_first_request(_set_blueprints_manager)
    app.before_first_request(_set_storage_manager)
    app.before_request(log_request)
    app.after_request(log_response)

    # saving flask's original error handlers
    flask_handle_exception = app.handle_exception
    flask_handle_user_exception = app.handle_user_exception

    api = Api(app)

    # saving flask-restful's error handlers
    flask_restful_handle_exception = app.handle_exception
    flask_restful_handle_user_exception = app.handle_user_exception

    # setting it so that <500 codes use flask-restful's error handlers,
    # while 500+ codes use original flask's error handlers (for which we
    # register an error handler on somewhere else in this module)
    def handle_exception(flask_method, flask_restful_method, e):
        code = getattr(e, 'code', 500)
        if code >= 500:
            return flask_method(e)
        else:
            return flask_restful_method(e)

    app.handle_exception = functools.partial(
        handle_exception,
        flask_handle_exception,
        flask_restful_handle_exception)
    app.handle_user_exception = functools.partial(
        handle_exception,
        flask_handle_user_exception,
        flask_restful_handle_user_exception)

    endpoint_mapper.setup_resources(api)
    return app


def log_request():
    # form and args parameters are "multidicts", i.e. values are not
    # flattened and will appear in a list (even if single value)
    form_data = request.form.to_dict(False)
    # args is the parsed query string data
    args_data = request.args.to_dict(False)
    # json data; other data (e.g. binary) is available via request.data,
    #  but is not logged
    json_data = request.json if hasattr(request, 'json') else None

    # content-type and content-length are already included in headers

    app.logger.debug(
        '\nRequest ({0}):\n'
        '\tpath: {1}\n'
        '\thttp method: {2}\n'
        '\tjson data: {3}\n'
        '\tquery string data: {4}\n'
        '\tform data: {5}\n'
        '\theaders: {6}'.format(
            id(request),
            request.path,  # includes "path parameters"
            request.method,
            json_data,
            args_data,
            form_data,
            headers_pretty_print(request.headers)))


def log_response(response):
    # content-type and content-length are already included in headers
    # not logging response.data as volumes are massive

    app.logger.debug(
        '\nResponse ({0}):\n'
        '\tstatus: {1}\n'
        '\theaders: {2}'
        .format(
            id(request),
            response.status,
            headers_pretty_print(response.headers)))
    return response


def headers_pretty_print(headers):
    pp_headers = ''.join(['\t\t{0}: {1}\n'.format(k, v) for k, v in headers])
    return '\n' + pp_headers


def reset_state(configuration=None):
    global app
    # print "resetting state in server"
    config.reset(configuration)
    # this doesn't really do anything
    # blueprints_manager.reset()
    storage_manager.reset()
    app = setup_app()


def create_logger(logger_name,
                  log_level,
                  log_file,
                  log_file_size_MB,
                  log_files_backup_count):

    additional_log_handlers = [
        RotatingFileHandler(
            filename=log_file,
            maxBytes=log_file_size_MB * 1024 * 1024,
            backupCount=log_files_backup_count)
    ]

    return utils.setup_logger(logger_name=logger_name,
                              logger_level=log_level,
                              handlers=additional_log_handlers,
                              remove_existing_handlers=False)


def _set_storage_manager():
    if not current_app.config.get('storage_manager'):
        sm = storage_manager.instance()
        app.logger.info('setting storage manager: {0}'.format(sm))
        current_app.config['storage_manager'] = sm


def _set_blueprints_manager():
    """
    create and set a blueprints manager for the current app context
    """
    if not current_app.config.get('blueprints_manager'):
        security_enabled = False
        ssl_enabled = False
        verify_ssl_certificate = False
        cloudify_username = None
        cloudify_password = None

        cfy_config = config.instance()
        if cfy_config.security_enabled:
            security_enabled = True
            if cfy_config.security_ssl.get('enabled', False):
                ssl_enabled = True
                verify_ssl_certificate = cfy_config.security_ssl.get(
                    'verify_certificate', True)
            cloudify_username = cfy_config.security_admin_username
            cloudify_password = cfy_config.security_admin_password

        app.logger.info('***** in _set_blueprints_manager, '
                        'security_enabled: {0}'.format(security_enabled))
        app.logger.info('***** in _set_blueprints_manager, '
                        'ssl_enabled: {0}'.format(ssl_enabled))
        app.logger.info('***** verify cert: {0}'.
                        format(verify_ssl_certificate))
        app.logger.info('***** cloudify username: {0}'.
                        format(cloudify_username))
        blueprints_mgr = blueprints_manager.BlueprintsManager(
            security_enabled, ssl_enabled, verify_ssl_certificate,
            cloudify_username, cloudify_password)
        app.logger.info('***** setting blueprints_manager: {0}'.
                        format(blueprints_mgr))
        current_app.config['blueprints_manager'] = blueprints_mgr


def init_secured_app(_app):
    cfy_config = config.instance()
    if cfy_config.security_auth_token_generator:
        register_auth_token_generator(
            _app, config.instance().security_auth_token_generator)

    # init and configure flask-securest
    secure_app = SecuREST(_app)
    secure_app.logger = create_logger(
        logger_name='flask-securest',
        log_level=cfy_config.security_audit_log_level,
        log_file=cfy_config.security_audit_log_file,
        log_file_size_MB=cfy_config.security_audit_log_file_size_MB,
        log_files_backup_count=cfy_config.security_audit_log_files_backup_count
    )

    if cfy_config.security_userstore_driver:
        register_userstore_driver(secure_app,
                                  cfy_config.security_userstore_driver)
    register_authentication_providers(
        secure_app, cfy_config.security_authentication_providers)

    register_authorization_provider(
        secure_app, cfy_config.security_authorization_provider)

    def unauthorized_user_handler():
        utils.abort_error(
            manager_exceptions.UnauthorizedError('user unauthorized'),
            current_app.logger,
            hide_server_message=True)

    secure_app.unauthorized_user_handler = unauthorized_user_handler

    def acl_handler():
        utils.abort_error(
            manager_exceptions.UnauthorizedError('user unauthorized'),
            current_app.logger,
            hide_server_message=True)

    secure_app.acl_handler = acl_handler
    secure_app.skip_auth_hook = _is_internal_request


def _is_internal_request(req):
    server_port = req.headers.get('X-Server-Port')
    return str(server_port) == SECURITY_BYPASS_PORT


def register_auth_token_generator(_app, auth_token_generator):
    _app.logger.debug('registering auth token generator {0}'
                      .format(auth_token_generator))
    _app.auth_token_generator = \
        utils.get_class_instance(auth_token_generator['implementation'],
                                 auth_token_generator['properties'])


def register_userstore_driver(secure_app, userstore_driver):
    secure_app.app.logger.debug('registering userstore driver {0}'
                                .format(userstore_driver))
    userstore = utils.get_class_instance(userstore_driver['implementation'],
                                         userstore_driver['properties'])
    secure_app.set_userstore_driver(userstore)


def register_authentication_providers(secure_app, authentication_providers):
    # Note: the order of registration is important here
    for provider in authentication_providers:
        secure_app.app.logger.debug('registering authentication provider {0}'
                                    .format(provider))
        auth_provider = utils.get_class_instance(provider['implementation'],
                                                 provider['properties'])
        secure_app.register_authentication_provider(provider['name'],
                                                    auth_provider)


def register_authorization_provider(secure_app, authorization_provider):
    secure_app.app.logger.debug('registering authorization provider {0}'
                                .format(authorization_provider))
    provider = utils.get_class_instance(
        authorization_provider['implementation'],
        authorization_provider['properties'])
    secure_app.set_authorization_provider(provider)


def load_configuration():
    obj_conf = config.instance()

    def load_config(env_var_name, namespace=''):
        if env_var_name in os.environ:
            with open(os.environ[env_var_name]) as f:
                yaml_conf = yaml.safe_load(f.read())
            for key, value in yaml_conf.iteritems():
                config_key = '{0}_{1}'.format(namespace, key) if namespace \
                    else key
                if hasattr(obj_conf, config_key):
                    setattr(obj_conf, config_key, value)

    load_config('MANAGER_REST_CONFIG_PATH')
    load_config('MANAGER_REST_SECURITY_CONFIG_PATH', 'security')

load_configuration()
print 'configuration loaded'
app = setup_app()


@app.errorhandler(500)
def internal_error(e):
    # app.logger.exception(e)  # gets logged automatically
    s_traceback = StringIO.StringIO()
    traceback.print_exc(file=s_traceback)

    response = jsonify(
        {"message":
         "Internal error occurred in manager REST server - {0}: {1}"
         .format(type(e).__name__, str(e)),
         "error_code": manager_exceptions.INTERNAL_SERVER_ERROR_CODE,
         "server_traceback": s_traceback.getvalue()})
    response.status_code = 500
    return response
