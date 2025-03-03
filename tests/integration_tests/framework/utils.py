import os
import sys
import time
import json
import shutil
import zipfile
import tempfile
import requests

from functools import wraps
from multiprocessing import Process
from contextlib import contextmanager

import pika
import ssl

from cloudify.utils import setup_logger
from cloudify_rest_client import CloudifyClient
from manager_rest.utils import create_auth_header

from cloudify_cli import env as cli_env
from cloudify_cli.constants import CLOUDIFY_BASE_DIRECTORY_NAME

from . import docker


logger = setup_logger('testenv.utils')


def _write(stream, s):
    try:
        s = s.encode('utf-8')
    except UnicodeDecodeError:
        pass
    stream.write(s)


def sh_bake(command):
    return command.bake(
        _out=lambda line: _write(sys.stdout, line),
        _err=lambda line: _write(sys.stderr, line))


def get_profile_context(container_id):
    profile_context_cmd =\
        'cat /root/.cloudify/profiles/manager-local/context.json'
    return json.loads(docker.execute(container_id, profile_context_cmd))


def set_cfy_paths(new_workdir):
    cli_env.CLOUDIFY_WORKDIR = os.path.join(
        new_workdir,
        CLOUDIFY_BASE_DIRECTORY_NAME
    )
    cli_env.PROFILES_DIR = os.path.join(cli_env.CLOUDIFY_WORKDIR, 'profiles')
    cli_env.ACTIVE_PRO_FILE = os.path.join(
        cli_env.CLOUDIFY_WORKDIR,
        'active.profile'
    )


def create_rest_client(host, **kwargs):
    # Doing it with kwargs instead of arguments with default values to allow
    # not passing args (which will then use the default values), or explicitly
    # passing None (or False) which will then be passed as-is to the Client

    username = kwargs.get('username', 'admin')
    password = kwargs.get('password', 'admin')
    tenant = kwargs.get('tenant', 'default_tenant')
    token = kwargs.get('token')
    rest_port = kwargs.get('rest_port', 443)
    rest_protocol = kwargs.get('rest_protocol',
                               'https' if rest_port == 443 else 'http')
    cert_path = kwargs.get('cert_path')
    trust_all = kwargs.get('trust_all', False)

    headers = create_auth_header(username, password, token, tenant)

    return CloudifyClient(
        host=host,
        port=rest_port,
        protocol=rest_protocol,
        headers=headers,
        trust_all=trust_all,
        cert=cert_path)


def create_pika_connection(container_id):
    host = docker.get_manager_ip(container_id)
    ca_data = docker.read_file(
        container_id,
        '/etc/cloudify/ssl/cloudify_internal_ca_cert.pem')

    credentials = pika.credentials.PlainCredentials(
        username='cloudify',
        password='c10udify')
    ssl_ctx = ssl.create_default_context(cadata=ca_data)
    return pika.BlockingConnection(
        pika.ConnectionParameters(host=host,
                                  port=5671,
                                  ssl_options=pika.SSLOptions(ssl_ctx),
                                  credentials=credentials))


def timeout(seconds=60):
    def decorator(func):
        def wrapper(*args, **kwargs):
            process = Process(None, func, None, args, kwargs)
            process.start()
            process.join(seconds)
            if process.is_alive():
                process.terminate()
                raise TimeoutException(
                    'test timeout exceeded [timeout={0}]'.format(seconds))
            if process.exitcode != 0:
                raise RuntimeError('{} ended with exception'.format(func))
        return wraps(func)(wrapper)
    return decorator


def timestamp():
    now = time.strftime("%c")
    return now.replace(' ', '-')


class TimeoutException(Exception):

    def __init__(self, message):
        Exception.__init__(self, message)

    def __str__(self):
        return self.message


@contextmanager
def zip_files(files):
    source_folder = tempfile.mkdtemp()
    destination_zip = source_folder + '.zip'
    for path in files:
        shutil.copy(path, source_folder)
    create_zip(source_folder, destination_zip, include_folder=False)
    shutil.rmtree(source_folder)
    try:
        yield destination_zip
    finally:
        os.remove(destination_zip)


def unzip(archive, destination):
    with zipfile.ZipFile(archive, 'r') as zip_file:
        zip_file.extractall(destination)


def create_zip(source, destination, include_folder=True):
    with zipfile.ZipFile(destination, 'w') as zip_file:
        for root, _, files in os.walk(source):
            for filename in files:
                file_path = os.path.join(root, filename)
                source_dir = os.path.dirname(source) if include_folder \
                    else source
                zip_file.write(
                    file_path, os.path.relpath(file_path, source_dir))
    return destination


def download_file(file_url, tmp_file):
    logger.info('Retrieving file: {0}'.format(file_url))
    response = requests.get(file_url, stream=True)
    with open(tmp_file, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    return tmp_file
