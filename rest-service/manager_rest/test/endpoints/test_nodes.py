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

from manager_rest.test.attribute import attr
from unittest import skip

from cloudify_rest_client.exceptions import CloudifyClientError

from manager_rest.storage import db, models
from manager_rest.test import base_test
from manager_rest import manager_exceptions
from manager_rest.test.mocks import put_node_instance


@attr(client_min_version=1, client_max_version=base_test.LATEST_API_VERSION)
class NodesTest(base_test.BaseServerTestCase):
    """Test the HTTP interface and the behaviour of node instance endpoints.

    Test cases that test the HTTP interface use shorthand methods like .patch()
    or .get() to call the rest service endpoints with hand-crafted data.
    Test cases that verify the behaviour use the rest client to construct
    the requests.
    """

    def test_get_nonexisting_node(self):
        response = self.get('/node-instances/1234')
        self.assertEqual(404, response.status_code)

    def test_get_node(self):
        put_node_instance(
            self.sm,
            instance_id='1234',
            deployment_id='111',
            runtime_properties={
                'key': 'value'
            }
        )
        response = self.get('/node-instances/1234')
        self.assertEqual(200, response.status_code)
        self.assertEqual('1234', response.json['id'])
        self.assertTrue('runtime_properties' in response.json)
        self.assertEqual(1, len(response.json['runtime_properties']))
        self.assertEqual('value', response.json['runtime_properties']['key'])

    @attr(client_min_version=3,
          client_max_version=base_test.LATEST_API_VERSION)
    def test_sort_nodes_list(self):
        self.put_deployment(deployment_id='d0', blueprint_id='b0')
        self.put_deployment(deployment_id='d1', blueprint_id='b1')

        nodes = self.client.nodes.list(sort='deployment_id')
        for i in range(len(nodes) - 1):
            self.assertTrue(
                nodes[i].deployment_id <= nodes[i + 1].deployment_id)

        nodes = self.client.nodes.list(
            sort='deployment_id', is_descending=True)
        for i in range(len(nodes) - 1):
            self.assertTrue(
                nodes[i].deployment_id >= nodes[i + 1].deployment_id)

    def test_bad_patch_node(self):
        """Malformed node instance update requests return an error."""
        response = self.patch('/node-instances/1234', 'not a dictionary')
        self.assertEqual(400, response.status_code)
        response = self.patch('/node-instances/1234', {
            'a dict': 'without '
                      'state_version'
                      ' key'})
        self.assertEqual(400, response.status_code)
        response = self.patch('/node-instances/1234', {
            'runtime_properties': {},
            'version': 'not an int'})
        self.assertEqual(400, response.status_code)

    def test_partial_patch_node(self):
        """PATCH requests with partial data are accepted."""
        put_node_instance(
            self.sm,
            instance_id='1234',
            deployment_id='111',
            runtime_properties={
                'key': 'value'
            },
            index=1,
        )

        # full patch
        response = self.patch('/node-instances/1234',
                              {
                                  'state': 'a-state',
                                  'runtime_properties': {'aaa': 'bbb'},
                                  'version': 1
                              })
        self.assertEqual(200, response.status_code)
        self.assertEqual('bbb', response.json['runtime_properties']['aaa'])
        self.assertEqual('a-state', response.json['state'])

        # patch with no runtime properties
        response = self.patch('/node-instances/1234', {'state': 'b-state',
                                                       'version': 2})
        self.assertEqual(200, response.status_code)
        self.assertEqual('bbb', response.json['runtime_properties']['aaa'])
        self.assertEqual('b-state', response.json['state'])

        # patch with neither state nor runtime properties
        response = self.patch('/node-instances/1234', {'version': 3})
        self.assertEqual(200, response.status_code)
        self.assertEqual('bbb', response.json['runtime_properties']['aaa'])
        self.assertEqual('b-state', response.json['state'])

        # patch with no state
        response = self.patch('/node-instances/1234',
                              {
                                  'runtime_properties': {'ccc': 'ddd'},
                                  'version': 4
                              })
        self.assertEqual(200, response.status_code)
        self.assertEqual('ddd', response.json['runtime_properties']['ccc'])
        self.assertEqual('b-state', response.json['state'])

    @skip('Deprecated since using sqlalchemy locking mechanism')
    def test_old_version(self):
        """Can't update a node instance passing new version != old version."""
        node_instance_id = '1234'
        put_node_instance(
            self.sm,
            instance_id=node_instance_id,
            deployment_id='111',
            runtime_properties={
                'key': 'value'
            }
        )

        with self.assertRaises(CloudifyClientError) as cm:
            self.client.node_instances.update(
                node_instance_id,
                version=2,  # Expecting version==1
                runtime_properties={'key': 'new value'})
        self.assertEqual(cm.exception.status_code, 409)

    def test_patch_node(self):
        """Getting an instance after updating it, returns the updated data."""
        node_instance_id = '1234'
        put_node_instance(
            self.sm,
            instance_id=node_instance_id,
            deployment_id='111',
            runtime_properties={
                'key': 'value'
            }
        )
        response = self.client.node_instances.update(
            node_instance_id,
            runtime_properties={'key': 'new_value', 'new_key': 'value'}
        )

        self.assertEqual(2, len(response.runtime_properties))
        self.assertEqual('new_value', response.runtime_properties['key'])
        self.assertEqual('value', response.runtime_properties['new_key'])

        response = self.client.node_instances.get(node_instance_id)

        self.assertEqual(2, len(response.runtime_properties))
        self.assertEqual('new_value', response.runtime_properties['key'])
        self.assertEqual('value', response.runtime_properties['new_key'])

    def test_patch_node_runtime_props_update(self):
        """Sending new runtime properties overwrites existing ones.

        The new runtime properties dict is stored as is, not merged with
        preexisting runtime properties.
        """
        node_instance_id = '1234'
        put_node_instance(
            self.sm,
            instance_id=node_instance_id,
            deployment_id='111',
            runtime_properties={
                'key': 'value'
            }
        )

        response = self.client.node_instances.update(
            node_instance_id,
            runtime_properties={'aaa': 'bbb'}
        )

        self.assertEqual('1234', response.id)
        self.assertEqual(1, len(response.runtime_properties))
        self.assertEqual('bbb', response.runtime_properties['aaa'])
        self.assertNotIn('key', response.runtime_properties)

    def test_patch_node_runtime_props_overwrite(self):
        """Runtime properties update with a preexisting key keeps the new value.

        When the new runtime properties have a key that was already in
        runtime properties, the new value wins.
        """
        node_instance_id = '1234'
        put_node_instance(
            self.sm,
            instance_id=node_instance_id,
            deployment_id='111',
            runtime_properties={
                'key': 'value'
            }
        )
        response = self.client.node_instances.update(
            node_instance_id,
            runtime_properties={'key': 'value2'}
        )
        self.assertEqual('1234', response.id)
        self.assertEqual(1, len(response.runtime_properties))
        self.assertEqual('value2', response.runtime_properties['key'])

    def test_patch_node_runtime_props_cleanup(self):
        """Sending empty runtime properties, removes preexisting ones."""
        node_instance_id = '1234'
        put_node_instance(
            self.sm,
            instance_id=node_instance_id,
            deployment_id='111',
            runtime_properties={
                'key': 'value'
            }
        )
        response = self.client.node_instances.update(
            node_instance_id,
            runtime_properties={}
        )
        self.assertEqual('1234', response['id'])
        self.assertEqual(0, len(response['runtime_properties']))

    def test_patch_node_conflict(self):
        """A conflict inside the storage manager propagates to the client."""
        # patch the storage manager .update_node_instance method to throw an
        # error - remember to revert it after the test
        def _revert_update_node_func(func):
            self.sm.update = func

        def conflict_update_node_func(node):
            db.session.rollback()
            raise manager_exceptions.ConflictError()

        node_instance_id = '1234'
        put_node_instance(
            self.sm,
            instance_id=node_instance_id,
            deployment_id='111',
            runtime_properties={
                'key': 'value'
            }
        )

        self.addCleanup(_revert_update_node_func, self.sm.update)
        self.sm.update = conflict_update_node_func

        with self.assertRaises(CloudifyClientError) as cm:
            self.client.node_instances.update(
                node_instance_id,
                runtime_properties={'key': 'new_value'},
                version=2
            )

        self.assertEqual(cm.exception.status_code, 409)

    @attr(client_min_version=2,
          client_max_version=base_test.LATEST_API_VERSION)
    def test_list_node_instances_multiple_value_filter(self):
        put_node_instance(
            self.sm, node_id='1', instance_id='11', deployment_id='111')
        put_node_instance(
            self.sm, node_id='1', instance_id='12', deployment_id='111')
        put_node_instance(
            self.sm, node_id='2', instance_id='21', deployment_id='111')
        put_node_instance(
            self.sm, node_id='2', instance_id='22', deployment_id='111')
        put_node_instance(
            self.sm, node_id='3', instance_id='31', deployment_id='222')
        put_node_instance(
            self.sm, node_id='3', instance_id='32', deployment_id='222')
        put_node_instance(
            self.sm, node_id='4', instance_id='41', deployment_id='222')
        put_node_instance(
            self.sm, node_id='4', instance_id='42', deployment_id='222')

        all_instances = self.client.node_instances.list()
        dep1_node_instances = \
            self.client.node_instances.list(
                deployment_id='111',
                node_id=['1', '2', '3', '4']
            )

        self.assertEqual(8, len(all_instances))
        self.assertEqual(4, len(dep1_node_instances))

    def test_list_node_instances(self):
        put_node_instance(
            self.sm, node_id='1', instance_id='11', deployment_id='111')
        put_node_instance(
            self.sm, node_id='1', instance_id='12', deployment_id='111')
        put_node_instance(
            self.sm, node_id='2', instance_id='21', deployment_id='111')
        put_node_instance(
            self.sm, node_id='2', instance_id='22', deployment_id='111')
        put_node_instance(
            self.sm, node_id='3', instance_id='31', deployment_id='222')
        put_node_instance(
            self.sm, node_id='3', instance_id='32', deployment_id='222')
        put_node_instance(
            self.sm, node_id='4', instance_id='41', deployment_id='222')
        put_node_instance(
            self.sm, node_id='4', instance_id='42', deployment_id='222')

        all_instances = self.client.node_instances.list()
        dep1_instances = self.client.node_instances.list(
            deployment_id='111')
        dep2_instances = self.client.node_instances.list(
            deployment_id='222')
        dep1_n1_instances = self.client.node_instances.list(
            deployment_id='111',
            node_name='1'
        )
        dep1_n2_instances = self.client.node_instances.list(
            deployment_id='111',
            node_name='2'
        )
        dep2_n3_instances = self.client.node_instances.list(
            deployment_id='222',
            node_name='3'
        )
        dep2_n4_instances = self.client.node_instances.list(
            deployment_id='222',
            node_name='4'
        )

        self.assertEqual(8, len(all_instances))

        def assert_dep(expected_len, dep, instances):
            self.assertEqual(expected_len, len(instances))
            for instance in instances:
                self.assertEqual(instance.deployment_id, dep)

        assert_dep(4, '111', dep1_instances)
        assert_dep(4, '222', dep2_instances)

        def assert_dep_and_node(expected_len, dep, node_id, instances):
            self.assertEqual(expected_len, len(instances))
            for instance in instances:
                self.assertEqual(instance.deployment_id, dep)
                self.assertEqual(instance.node_id, node_id)

        assert_dep_and_node(2, '111', '1', dep1_n1_instances)
        assert_dep_and_node(2, '111', '2', dep1_n2_instances)
        assert_dep_and_node(2, '222', '3', dep2_n3_instances)
        assert_dep_and_node(2, '222', '4', dep2_n4_instances)

    @attr(client_min_version=3,
          client_max_version=base_test.LATEST_API_VERSION)
    def test_sort_node_instances_list(self):
        put_node_instance(
            self.sm, node_id='0', instance_id='00', deployment_id='000')
        put_node_instance(
            self.sm, node_id='1', instance_id='11', deployment_id='111')

        instances = self.client.node_instances.list(sort='node_id')
        self.assertEqual(2, len(instances))
        self.assertEqual('00', instances[0].id)
        self.assertEqual('11', instances[1].id)

        instances = self.client.node_instances.list(
            sort='node_id', is_descending=True)
        self.assertEqual(2, len(instances))
        self.assertEqual('11', instances[0].id)
        self.assertEqual('00', instances[1].id)

    def test_patch_before_put(self):
        """Updating a nonexistent node instance throws an error."""
        with self.assertRaises(CloudifyClientError) as cm:
            self.client.node_instances.update(
                '1234',
                runtime_properties={'key': 'value'}
            )

        self.assertEqual(cm.exception.status_code, 404)

    @attr(client_min_version=2.1,
          client_max_version=base_test.LATEST_API_VERSION)
    def test_node_and_node_instance_properties(self):
        _, _, _, deployment = self.put_deployment(
            blueprint_file_name='deployment-creation-with-groups.yaml')
        node = self.client.nodes.get(deployment.id, 'vm')
        self.assertDictContainsSubset({
            'number_of_instances': 2,
            'min_number_of_instances': 2,
            'max_number_of_instances': 2,
            'planned_number_of_instances': 2,
            'deploy_number_of_instances': 2,
        }, node)
        instances = self.client.node_instances.list(
            deployment_id=deployment.id
        ).items
        for instance in instances:
            # test list/get/patch endpoints
            tested_instances = [
                instance,
                self.client.node_instances.get(instance.id),
                self.client.node_instances.update(instance.id)]
            for tested_instance in tested_instances:
                scaling_groups = tested_instance.scaling_groups
                self.assertEqual(1, len(scaling_groups))
                self.assertDictContainsSubset({'name': 'group1'},
                                              scaling_groups[0])


class NodesCreateTest(base_test.BaseServerTestCase):
    def test_create_nodes(self):
        self.put_deployment('dep1')
        self.client.nodes.create_many('dep1', [
            {
                'id': 'test_node1',
                'type': 'cloudify.nodes.Root'
            },
            {
                'id': 'test_node2',
                'type': 'cloudify.nodes.Root'
            }
        ])
        nodes = self.sm.list(models.Node)
        node1 = [n for n in nodes if n.id == 'test_node1']
        node2 = [n for n in nodes if n.id == 'test_node2']
        assert len(node1) == 1
        node1 = node1[0]
        assert len(node2) == 1
        assert node1.deployment_id == 'dep1'

    def test_create_parameters(self):
        self.put_deployment('dep1')
        # those values don't necessarily make sense, but let's test that
        # all of them are passed through correctly
        node_type = 'node_type1'
        type_hierarchy = ['cloudify.nodes.Root', 'base_type', 'node_type1']
        relationships = [{
            'target_id': 'node1',
            'type': 'relationship_type1',
            'type_hierarchy': ['relationship_type1'],
            'properties': {'a': 'b'},
            'source_operations': {},
            'target_operations': {},
        }]
        properties = {'prop1': 'value'}
        operations = {'op1': 'operation'}
        current_instances = 3
        default_instances = 3
        min_instances = 2
        max_instances = 5
        plugins = ['plug1']
        self.client.nodes.create_many('dep1', [
            {
                'id': 'test_node1',
                'type': node_type,
                'type_hierarchy': type_hierarchy,
                'properties': properties,
                'relationships': relationships,
                'operations': operations,
                'plugins': plugins,
                'capabilities': {
                    'scalable': {
                        'properties': {
                            'current_instances': current_instances,
                            'default_instances': default_instances,
                            'min_instances': min_instances,
                            'max_instances': max_instances,
                        }
                    }
                }
            }
        ])
        deployment = self.sm.get(models.Deployment, 'dep1')
        node = self.sm.get(models.Node, 'test_node1')
        assert node.deployment == deployment
        assert node.type_hierarchy == type_hierarchy
        assert node.type == node_type
        assert node.properties == properties
        assert node.relationships == relationships
        assert node.plugins == plugins
        assert node.operations == operations
        assert node.number_of_instances == current_instances
        assert node.planned_number_of_instances == current_instances
        assert node.deploy_number_of_instances == default_instances
        assert node.min_number_of_instances == min_instances
        assert node.max_number_of_instances == max_instances

    def test_empty_list(self):
        self.client.nodes.create_many('dep1', [])  # doesn't throw


class NodeInstancesCreateTest(base_test.BaseServerTestCase):
    def test_empty_list(self):
        self.client.node_instances.create_many('dep1', [])  # doesn't throw

    def test_create_instances(self):
        self.put_deployment('dep1')
        self.client.nodes.create_many('dep1', [
            {
                'id': 'test_node1',
                'type': 'cloudify.nodes.Root'
            }
        ])
        self.client.node_instances.create_many('dep1', [
            {
                'id': 'test_node1_xyz123',
                'node_id': 'test_node1'
            }
        ])
        node = self.sm.get(models.Node, 'test_node1')
        node_instance = self.sm.get(models.NodeInstance, 'test_node1_xyz123')
        assert node_instance.node == node

    def test_instance_index(self):
        self.put_deployment('dep1')
        self.client.nodes.create_many('dep1', [
            {
                'id': 'test_node1',
                'type': 'cloudify.nodes.Root'
            },
            {
                'id': 'test_node2',
                'type': 'cloudify.nodes.Root'
            },
        ])
        self.client.node_instances.create_many('dep1', [
            {
                'id': 'test_node1_1',
                'node_id': 'test_node1'
            }
        ])
        instance1 = self.sm.get(models.NodeInstance, 'test_node1_1')
        self.client.node_instances.create_many('dep1', [
            {
                'id': 'test_node1_2',
                'node_id': 'test_node1'
            },
            {
                'id': 'test_node2_1',
                'node_id': 'test_node2'
            },
        ])
        instance2 = self.sm.get(models.NodeInstance, 'test_node1_2')
        node2_instance1 = self.sm.get(models.NodeInstance, 'test_node2_1')
        assert instance1.index == 1
        assert instance2.index == 2
        assert node2_instance1.index == 1
