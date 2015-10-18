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


from manager_rest.celery_client import celery_client as client


class WorkflowClient(object):

    def __init__(self, security_enabled, ssl_enabled, verify_ssl_certificate,
                 admin_username, admin_password):
        self.security_enabled = security_enabled
        self.ssl_enabled = ssl_enabled
        self.verify_ssl_certificate = verify_ssl_certificate
        self.admin_username = admin_username
        self.admin_password = admin_password

    @staticmethod
    def execute_workflow(name,
                         workflow,
                         blueprint_id,
                         deployment_id,
                         execution_id,
                         execution_parameters=None):
        task_name = workflow['operation']
        task_queue = '{}_workflows'.format(deployment_id)

        execution_parameters['__cloudify_context'] = {
            'workflow_id': name,
            'blueprint_id': blueprint_id,
            'deployment_id': deployment_id,
            'execution_id': execution_id
        }
        print '***** in execute_workflow, calling execute_task ' \
              'with __cloudify_context: {0}'.\
            format(execution_parameters['__cloudify_context'])
        client().execute_task(task_name=task_name,
                              task_queue=task_queue,
                              task_id=execution_id,
                              kwargs=execution_parameters)

    def execute_system_workflow(self, deployment, wf_id, task_id, task_mapping,
                                execution_parameters=None):
        # task_id is not generated here since for system workflows,
        # the task id is equivalent to the execution id

        task_queue = 'cloudify.management'

        context = {
            'task_id': task_id,
            'task_name': task_mapping,
            'task_target': task_queue,
            'blueprint_id': deployment.blueprint_id,
            'deployment_id': deployment.id,
            'execution_id': task_id,
            'workflow_id': wf_id,
            'cloudify_username': self.admin_username,
            'cloudify_password': self.admin_password,
            'security_enabled': self.security_enabled,
            'ssl_enabled': self.ssl_enabled,
            'verify_ssl_certificate': self.verify_ssl_certificate
        }
        execution_parameters = execution_parameters or {}
        print '***** in execute_system_workflow, setting __cloudify_context' \
              ' to: {0}'.format(context)
        execution_parameters['__cloudify_context'] = context

        return client().execute_task(
            task_mapping,
            task_queue,
            task_id,
            kwargs=execution_parameters)


def workflow_client(security_enabled, ssl_enabled, verify_ssl_certificate,
                    admin_username, admin_password):
    return WorkflowClient(security_enabled,
                          ssl_enabled,
                          verify_ssl_certificate,
                          admin_username,
                          admin_password)
