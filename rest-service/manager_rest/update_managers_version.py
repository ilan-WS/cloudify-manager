#!/opt/manager/env/bin/python

import os
import sys
import socket
import logging
import argparse

from manager_rest import config
from manager_rest.flask_utils import setup_flask_app
from manager_rest.storage import models, get_storage_manager

logger = logging.getLogger(__name__)


def update_managers_version(version):
    logger.debug('Updating Cloudify managers version in DB...')
    sm = get_storage_manager()
    managers = sm.full_access_list(models.Manager)

    hostname = socket.gethostname()
    if hasattr(config.instance, 'manager_hostname'):
        hostname = config.instance.manager_hostname

    for manager in managers:
        if manager.hostname == hostname:
            manager.version = version
            sm.update(manager)
            break


if __name__ == '__main__':
    with setup_flask_app().app_context():
        config.instance.load_configuration()
    parser = argparse.ArgumentParser(
        description="Update the Cloudify manager version in the DB")
    parser.add_argument('version')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format='%(levelname)s %(asctime)s %(message)s',
                        stream=sys.stderr)
    if os.geteuid() != 0:
        raise RuntimeError('This script must be run as root!')
    update_managers_version(args.version)
