#########
# Copyright (c) 2019 Cloudify Platform Ltd. All rights reserved
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

from setuptools import setup


setup(
    name='cloudify-types',
    version='6.1.0.dev1',
    author='Cloudify',
    author_email='cosmo-admin@cloudify.co',
    packages=[
        'cloudify_types',
        'cloudify_types.component',
        'cloudify_types.shared_resource'
    ],
    license='LICENSE',
    description='Various special Cloudify types implementation.',
    install_requires=[
        'cloudify-common==6.1.0.dev1',
        'requests>=2.25.0,<3.0.0',
        'PyYAML==5.4.1',
    ]
)
