########
# Copyright (c) 2013 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#    * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    * See the License for the specific language governing permissions and
#    * limitations under the License.

import os
import yaml
import uuid
import tempfile

from integration_tests.tests import utils
from integration_tests.framework import docl
from integration_tests import AgentlessTestCase


class TestHooks(AgentlessTestCase):
    HOOKS_CONFIG_PATH = '/opt/mgmtworker/config/hooks.conf'
    LOG_PATH = '/var/log/cloudify/mgmtworker/mgmtworker.log'
    PLUGIN_LOG_PATH = '/tmp/hook_task.txt'

    def test_missing_compatible_hook(self):
        new_config = """
hooks:
  - event_type: test_event_type
    hook_type: test_hook_type
    implementation: package.module.task
    inputs:
      input1: bla
      input2: bla
    description: test hook
"""
        self._update_hooks_config(new_config)
        self._start_a_workflow()
        workflow_started_msg = "received `workflow_started` event but " \
                               "didn't find a compatible hook"
        workflow_succeeded_msg = "received `workflow_succeeded` event but " \
                                 "didn't find a compatible hook"
        self._assert_messages_in_log([workflow_started_msg,
                                      workflow_succeeded_msg])

    def test_invalid_implementation_module(self):
        new_config = """
hooks:
  - event_type: workflow_started
    hook_type: workflow_started_hook
    implementation: package.module.task
    inputs:
      input1: bla
      input2: bla
    description: test hook
"""
        self._update_hooks_config(new_config)
        self._start_a_workflow()
        workflow_started_msg = "received `workflow_started` event and the " \
                               "hook type is: `workflow_started_hook`"
        invalid_implementation_msg = "No module named package.module"
        workflow_succeeded_msg = "received `workflow_succeeded` event but " \
                                 "didn't find a compatible hook"
        self._assert_messages_in_log([workflow_started_msg,
                                      invalid_implementation_msg,
                                      workflow_succeeded_msg])

    def test_invalid_implementation_task(self):
        new_config = """
hooks:
  - event_type: workflow_started
    hook_type: workflow_started_hook
    implementation: cloudmock.tasks.test
    inputs:
      input1: bla
      input2: bla
    description: test hook
"""
        self._update_hooks_config(new_config)
        self._start_a_workflow()
        workflow_started_msg = "received `workflow_started` event and the " \
                               "hook type is: `workflow_started_hook`"
        invalid_task_msg = "cloudmock.tasks has no function named \\'test\\'"
        workflow_succeeded_msg = "received `workflow_succeeded` event but " \
                                 "didn't find a compatible hook"
        self._assert_messages_in_log([workflow_started_msg,
                                      invalid_task_msg,
                                      workflow_succeeded_msg])

    def test_missing_implementation(self):
        new_config = """
hooks:
  - event_type: workflow_started
    hook_type: workflow_started_hook
    inputs:
      input1: bla
      input2: bla
    description: test hook
"""
        self._update_hooks_config(new_config)
        self._start_a_workflow()
        error_msg = "ERROR - KeyError('implementation',), while running " \
                    "hook: workflow_started_hook triggered by the event: " \
                    "workflow_started"
        workflow_succeeded_msg = "received `workflow_succeeded` event but " \
                                 "didn't find a compatible hook"
        self._assert_messages_in_log([error_msg,
                                      workflow_succeeded_msg])

    def test_missing_inputs(self):
        new_config = """
hooks:
  - event_type: workflow_started
    hook_type: workflow_started_hook
    implementation: cloudmock.tasks.hook_task
    description: test hook
"""
        self._update_hooks_config(new_config)
        self._start_a_workflow()
        event_type_msg = "workflow_started"
        kwargs_msg = "kwargs: {}"
        self._assert_messages_in_log([event_type_msg, kwargs_msg],
                                     log_path=self.PLUGIN_LOG_PATH)

    def test_invalid_hooks_config(self):
        new_config = """
test_hook:
    invalid: true
"""
        self._update_hooks_config(new_config)
        self._start_a_workflow()
        workflow_started_error = "ERROR - The hook consumer received " \
                                 "`workflow_started` event but the hook " \
                                 "config file is invalid"
        workflow_succeeded_error = "ERROR - The hook consumer received " \
                                   "`workflow_succeeded` event but the " \
                                   "hook config file is invalid"
        self._assert_messages_in_log([workflow_started_error,
                                      workflow_succeeded_error])

    def test_missing_hook_config(self):
        self.delete_manager_file(self.HOOKS_CONFIG_PATH)
        self._start_a_workflow()
        workflow_started_msg = "The hook consumer received " \
                               "`workflow_started` event but the " \
                               "hook config file doesn't exist"
        workflow_succeeded_msg = "The hook consumer received " \
                                 "`workflow_succeeded` event but the " \
                                 "hook config file doesn't exist"
        self._assert_messages_in_log([workflow_started_msg,
                                      workflow_succeeded_msg])

    def test_implementation_plugin(self):
        new_config = """
hooks:
  - event_type: workflow_started
    hook_type: workflow_started_hook
    implementation: cloudmock.tasks.hook_task
    inputs:
      input1: input1_test
      input2: input2_test
    description: test hook
"""
        self._update_hooks_config(new_config)
        self._start_a_workflow()
        event_type_msg = "workflow_started"
        workflow_id_msg = "create_deployment_environment"
        input1_msg = "input1_test"
        messages = [event_type_msg, workflow_id_msg, input1_msg]
        self._assert_messages_in_log(messages, log_path=self.PLUGIN_LOG_PATH)

    def test_implementation_function(self):
        new_config = """
hooks:
  - event_type: workflow_started
    hook_type: workflow_started_hook
    implementation: cloudify.tests.mocks.mock_module.mock_hook_function
    inputs:
      input1: input1_test
      input2: input2_test
    description: test hook
"""
        self._update_hooks_config(new_config)
        self._start_a_workflow()
        event_type_msg = "workflow_started"
        workflow_id_msg = "create_deployment_environment"
        input1_msg = "input1_test"
        input2_msg = "input2_test"
        messages = [event_type_msg, workflow_id_msg, input1_msg, input2_msg]
        self._assert_messages_in_log(messages,
                                     log_path='/tmp/mock_hook_function.txt')

    def test_multiple_hooks(self):
        new_config = """
hooks:
  - event_type: workflow_started
    hook_type: workflow_started_hook
    implementation: cloudify.tests.mocks.mock_module.mock_hook_function
    inputs:
      input1: input1_workflow_started
      input2: input2_workflow_started
    description: test hook
  - event_type: workflow_succeeded
    hook_type: workflow_succeeded_hook
    implementation: cloudify.tests.mocks.mock_module.mock_hook_function
    inputs:
      input1: input1_workflow_succeeded
      input2: input2_workflow_succeeded
    description: test hook
  - event_type: workflow_failed
    hook_type: workflow_failed_hook
    implementation: cloudify.tests.mocks.mock_module.mock_hook_function
    inputs:
      input1: input1_workflow_failed
      input2: input2_workflow_failed
    description: test hook
"""
        self._update_hooks_config(new_config)
        self._start_a_workflow()
        started_event_type_msg = "workflow_started"
        succeeded_event_type_msg = "workflow_succeeded"
        workflow_id_msg = "create_deployment_environment"
        started_kwargs_msg = "input1_workflow_started"
        succeeded_kwargs_msg = "input2_workflow_succeeded"
        messages = [started_event_type_msg, succeeded_event_type_msg,
                    workflow_id_msg, started_kwargs_msg, succeeded_kwargs_msg]
        self._assert_messages_in_log(messages,
                                     log_path='/tmp/mock_hook_function.txt')

    def _start_a_workflow(self):
        # Start the create deployment workflow
        dsl_path = utils.get_resource('dsl/basic.yaml')
        blueprint_id = deployment_id = 'basic_{}'.format(uuid.uuid4())
        self.client.blueprints.upload(dsl_path, blueprint_id)
        self.client.deployments.create(blueprint_id,
                                       deployment_id)
        utils.wait_for_deployment_creation_to_complete(deployment_id)
        return deployment_id

    def _assert_messages_in_log(self, messages, log_path=LOG_PATH):
        tmp_log_path = os.path.join(self.workdir, 'test_log')
        docl.copy_file_from_manager(log_path, tmp_log_path)
        with open(tmp_log_path) as f:
            data = f.readlines()
        last_log_lines = str(data[-10:])
        for message in messages:
            assert message in last_log_lines

    def _update_hooks_config(self, new_config):
        with tempfile.NamedTemporaryFile() as f:
            yaml.dump(yaml.load(new_config), f, default_flow_style=False)
            f.flush()
            docl.copy_file_to_manager(source=f.name,
                                      target=self.HOOKS_CONFIG_PATH)
            docl.execute('chown cfyuser: {0}'.format(self.HOOKS_CONFIG_PATH))
