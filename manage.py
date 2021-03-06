#! /usr/bin/env python

import json
import os
import signal
import subprocess
import time

import click
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

APPLICATION_CONFIG_PATH = 'config'
DOCKER_PATH = 'docker'


def setenv(variable, default):
    os.environ[variable] = os.getenv(variable, default)


def app_config_file(config):
    return os.path.join(APPLICATION_CONFIG_PATH, f'{config}.json')


def docker_compose_file(config):
    return os.path.join(DOCKER_PATH, f'{config}.yml')


def read_json_configuration(config):
    # Read configuration from the relative JSON file
    with open(app_config_file(config)) as f:
        config_data = json.load(f)
    # Convert the config into a usable Python dictionary
    config_data = dict((i['name'], i['value']) for i in config_data)

    return config_data


def configure_app(config):
    configuration = read_json_configuration(config)

    for key, value in configuration.items():
        setenv(key, value)


def docker_compose_cmdline(commands_string):
    config = os.getenv('FASTAPI_ENV')
    configure_app(config)

    compose_file = docker_compose_file(config)

    if not os.path.isfile(compose_file):
        raise ValueError(f'The file {compose_file} does not exist')

    command_line = [
        'docker-compose',
        '-p',
        config,
        '-f',
        compose_file
    ]

    if commands_string:
        command_line.extend(commands_string.split(' '))

    return command_line


def run_sql(statements):
    conn = psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
    )

    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()
    for statement in statements:
        cursor.execute(statement)

    cursor.close()
    conn.close()


def wait_for_logs(cmdline, message):
    logs = subprocess.check_output(cmdline)
    while message not in logs.decode('utf-8'):
        time.sleep(1)
        logs = subprocess.check_output(cmdline)


@click.group()
def cli():
    pass


@cli.command(context_settings={'ignore_unknown_options': True})
@click.argument('subcommand', nargs=-1, type=click.Path())
def start(subcommand):
    configure_app(os.getenv('FASTAPI_ENV'))
    application_cmdline = ['uvicorn', 'application.main:app'] + list(subcommand)

    try:
        application_process = subprocess.Popen(application_cmdline)
        application_process.wait()
    except KeyboardInterrupt:
        application_process.send_signal(signal.SIGINT)
        application_process.wait()


@cli.command(context_settings={'ignore_unknown_options': True})
@click.argument('subcommand', nargs=-1, type=click.Path())
def compose(subcommand):
    configure_app(os.getenv('FASTAPI_ENV'))
    docker_cmdline = docker_compose_cmdline('') + list(subcommand)

    try:
        docker_process = subprocess.Popen(docker_cmdline)
        docker_process.wait()
    except KeyboardInterrupt:
        docker_process.send_signal(signal.SIGINT)
        docker_process.wait()
        
        
@cli.command(context_settings={'ignore_unknown_options': True})
@click.argument('args', nargs=-1)
def test(args):
    os.environ['FASTAPI_ENV'] = 'testing'
    configure_app(os.getenv('FASTAPI_ENV'))

    cmdline = docker_compose_cmdline('up -d')
    subprocess.call(cmdline)

    cmdline = docker_compose_cmdline("logs db")
    wait_for_logs(cmdline, 'ready to accept connections')

    try:
        run_sql([f"CREATE DATABASE {os.getenv('FASTAPI_ENV')}"])

        cmdline = [
            'pytest',
            '-svv',
            '--cov=application',
            '--cov-report=term-missing'
        ]
        cmdline.extend(args)
        subprocess.call(cmdline)
    finally:
        run_sql([f"DROP DATABASE {os.getenv('FASTAPI_ENV')}"])
        cmdline = docker_compose_cmdline('down')
        subprocess.call(cmdline)


if __name__ == '__main__':
    cli()
