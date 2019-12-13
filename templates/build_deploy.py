#!/usr/bin/python{{ ((ansible_os_family == "RedHat" and ansible_distribution_major_version|int >= 8) or (ansible_os_family == "Debian" and ansible_distribution_major_version|int >= 10)) | ternary('3', '') }}
#
# Copyright (c) 2015 Michael Scherer <mscherer@redhat.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# This script trigger a middleman rebuild if upstream git or any submodule
# did change


import yaml
import sys
import os
import errno
import subprocess
import datetime
import atexit
import syslog
import argparse
import shutil


parser = argparse.ArgumentParser(description="Build middleman sites based "
                                             "on changes in git")
parser.add_argument("-f", "--force", help="force the rebuild",
                    action="store_true")
parser.add_argument("-n", "--dry-run", help="do not sync remotely",
                    action="store_true")
parser.add_argument("-d", "--debug", help="show debug output",
                    action="store_true")
parser.add_argument("-s", "--sync-only", help="do not build, only sync",
                    action="store_true")
parser.add_argument("config_file", help="yaml file for the builder config")
parser.add_argument("--no-refresh", help="do not refresh the checkout",
                    action="store_true")
args = parser.parse_args()


builder_info = {
    'middleman': {
        'build_env': {},
        'build_command': ['bundle', 'exec', 'middleman', 'build', '--verbose'],
        'build_subdir': 'build',
        'deploy_command': ['bundle', 'exec', 'middleman', 'deploy', '--no-build-before']
    },
    # Duck: we had an incomplete build for Pulp (new post but blog index not updated)
    #       disabling --incremental mode for now
    'jekyll': {
        'build_env': {
            'JEKYLL_ENV': 'production'
        },
        'build_command': ['bundle', 'exec', 'jekyll', 'build', '--verbose', '--trace'],
        'build_subdir': '_site',
        'deploy_command': None
    },
    'ascii_binder': {
        'build_env': {},
        'build_command': ['bundle', 'exec', 'asciibinder', 'package', '--site=main', '--log-level=debug'],
        'build_subdir': '_package/main',
        'deploy_command': None
    },
    'planet': {
        'build_env': {},
        'build_command': ['/srv/builder/planet-venus/planet.py', '-v', 'planet.ini'],
        'build_subdir': 'build',
        'deploy_command': None
    },
    'nikola': {
        'build_env': {},
        'build_command': ['../.local/bin/nikola', 'build'],
        'build_subdir': 'output',
        'deploy_command': None
    }
}


def log_print(message):
    try:
        log_fd
    except NameError:
        pass
    else:
        log_fd.write(message + "\n")
        log_fd.flush()


def debug_print(message):
    if args.debug:
        print(message)
    log_print(message)


def refresh_checkout(checkout_dir):
    os.chdir(checkout_dir)
    try:
        result = subprocess.check_output(['git', 'fetch', '-q'], stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as C:
        notify_error('setup', C.output)
    debug_print(result.decode())


def get_last_commit(checkout_dir):
    os.chdir(checkout_dir)
    try:
        r = subprocess.check_output(['git', 'ls-remote', '-q', '.',
                                     'refs/remotes/origin/%s' % config['git_version']])
    except subprocess.CalledProcessError as C:
        notify_error('setup', C.output)
    return r.decode().split()[0]


def get_last_commit_submodule(checkout_dir, submodule):
    os.chdir("{}/{}/".format(checkout_dir, submodule))
    try:
        r = subprocess.check_output(['git', 'ls-remote', '-q', '.',
                                     'refs/remotes/origin/HEAD'])
    except subprocess.CalledProcessError as C:
        notify_error('setup', C.output)
    return r.decode().split()[0]


def get_submodules_checkout(checkout_dir):
    os.chdir(checkout_dir)
    result = []
    try:
        submodule_status = subprocess.check_output(['git', 'submodule', 'status'])
    except subprocess.CalledProcessError as C:
        notify_error('setup', C.output)
    for s in submodule_status.decode().split('\n'):
        # there is a empty line at the end...
        if s:
            result.append(s.split()[1])
    return result


def load_config(config_file):
    if not os.path.exists(config_file):
        print("Error %s, do not exist" % config_file)
        sys.exit(1)
    if not os.path.isfile(config_file):
        print("Error %s is not a file" % config_file)
        sys.exit(1)

    with open(config_file) as f:
        config = yaml.safe_load(f)

    return config


def has_submodules(checkout_dir):
    os.chdir(checkout_dir)
    try:
        r = subprocess.check_output(['git', 'submodule', 'status'])
    except subprocess.CalledProcessError as C:
        notify_error('setup', C.output)
    return len(r.decode()) > 0


# TODO complete that
def notify_error(stage, error):
    print(error)
    sys.exit(3)


def do_rsync(source):
    try:
        r = subprocess.check_output(
               ['rsync',
                '-e',
                'ssh '
                '-o '
                'UserKnownHostsFile=/dev/null '
                '-o '
                'StrictHostKeyChecking=no '
                '-i ' +
                os.path.expanduser('~/.ssh/{}_id.rsa'.format(name)),
                '--delete-after',
                '-rltogvz',
                '--omit-dir-times',
                source,
                config['remote']], stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as C:
        notify_error('setup', C.output)
    return r.decode()



if not args.config_file:
    print("This script take only 1 single argument, the config file")
    sys.exit(1)

config = load_config(args.config_file)

if 'name' not in config:
    print("Incorrect config file: {}".format(args.config_file))
    sys.exit(1)

name = config['name']

lock_file = os.path.expanduser('{}/lock_{}'.format(
    os.environ.get('XDG_RUNTIME_DIR', '~'), name))
if os.path.exists(lock_file):
    # TODO verify if the PID in the file still exist
    debug_print("Builder already running, exiting")
    sys.exit(2)

# TODO try/except, show a better error message
fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
os.write(fd, str(os.getpid()).encode())
os.close(fd)
atexit.register(os.unlink, lock_file)

status_file = os.path.expanduser('~/status_%s.yml' % name)
status = {}
if os.path.exists(status_file):
    with open(status_file) as f:
        status = yaml.safe_load(f)

checkout_dir = os.path.expanduser("~/%s" % name)
if not os.path.isdir(checkout_dir):
    os.unlink(lock_file)
    print("Checkout not existing, exiting")
    sys.exit(2)

refresh_checkout(checkout_dir)

start_build = False

last_build = datetime.datetime.fromtimestamp(
    int(status.get("last_build", "0")))

if ('regular_rebuild_interval' in config and
        datetime.datetime.now() - last_build >
        datetime.timedelta(hours=config['regular_rebuild_interval'])):
    start_build = True

current_commit = get_last_commit(checkout_dir)
if current_commit != status.get('last_build_commit', ''):
    start_build = True

submodule_commits = status.get('submodule_commits', {})
current_submodule_commits = {}
for submodule in get_submodules_checkout(checkout_dir):
    debug_print('Looking for %s' % submodule)
    current_submodule_commits[submodule] = \
        get_last_commit_submodule(checkout_dir, submodule)

    if current_submodule_commits[submodule] != \
            submodule_commits.get(submodule, ''):
        start_build = True

if args.force:
    start_build = True

if not start_build:
    debug_print("Nothing to build")
    sys.exit(0)

# use an Unicode-enabled locale
os.environ['LC_ALL'] = 'en_US.UTF-8'
os.environ['LANG'] = 'en_US.UTF-8'
os.environ['LANGUAGE'] = 'en_US.UTF-8'

# Do not open earlier or we would end-up logging a lot of
# "Nothing to build" messages and lose the last build log.
log_file = os.path.expanduser('~/%s.log' % name)
log_fd = open(log_file, "w")
log_fd.write("last_build_date: {}\n".format(datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S (UTC)")))
log_fd.write("last_build_commit: {}\n".format(current_commit))
log_fd.write("submodule_commits: {}\n".format(current_submodule_commits))
log_fd.write("\n")

syslog.syslog("Start the build of {}".format(name))

os.chdir(checkout_dir)
if not args.no_refresh:
    try:
        result = subprocess.check_output(['git', 'stash'], stderr=subprocess.STDOUT)
        debug_print(result.decode())
        result = subprocess.check_output(['git', 'stash', 'clear'], stderr=subprocess.STDOUT)
        debug_print(result.decode())
        result = subprocess.check_output(['git', 'pull', '--rebase'], stderr=subprocess.STDOUT)
        debug_print(result.decode())
    except subprocess.CalledProcessError as C:
        notify_error('setup', C.output)

if has_submodules(checkout_dir):
    try:
        result = subprocess.check_output(['git', 'submodule', 'init'], stderr=subprocess.STDOUT)
        debug_print(result.decode())
        result = subprocess.check_output(['git', 'submodule', 'sync'], stderr=subprocess.STDOUT)
        debug_print(result.decode())
    except subprocess.CalledProcessError as C:
        notify_error('setup', C.output)

build_subdir = builder_info[config['builder']]['build_subdir']
build_dir = '%s/%s' % (checkout_dir, build_subdir)

# ensure build directory exist or creating the sync log would fail
try:
    # TODO: use exist_ok instead of all this crap when switching to Python 3
    os.makedirs(build_dir, mode=0o775)
except OSError as exc:
    if exc.errno == errno.EEXIST and os.path.isdir(build_dir):
        pass
    else:
        raise

# Using a .txt extension to ensure webservers will allow access to it
sync_log_path = '%s/build_log.txt' % build_dir

if not args.sync_only:
    os.environ['PATH'] = "/usr/local/bin:/srv/builder/bin:" + \
                         os.environ['PATH']

    if os.path.exists('Gemfile'):
        try:
            syslog.syslog("Build of {}: bundle install".format(name))
            # don't use embedded libraries to build Nokogiri
            os.environ['NOKOGIRI_USE_SYSTEM_LIBRARIES'] = '1'
            result = subprocess.check_output(['bundle', 'install'], stderr=subprocess.STDOUT)
            debug_print(result.decode())
        except subprocess.CalledProcessError as C:
            log_print(C.output)
            if config['remote']:
                # copy log in build dir and sync it to make it available to users
                shutil.copy2(log_file, sync_log_path)
                try:
                    do_rsync(sync_log_path)
                except subprocess.CalledProcessError:
                    pass
            notify_error('install', C.output)

    # set environment
    for env_name, env_value in builder_info[config['builder']]['build_env'].items():
        os.environ[env_name] = env_value

    try:
        command = builder_info[config['builder']]['build_command']
        syslog.syslog("Build of {}: {}".format(name, ' '.join(command)))
        result = subprocess.check_output(command, stderr=subprocess.STDOUT)
        debug_print(result.decode())
    except subprocess.CalledProcessError as C:
        log_print(C.output)
        if config['remote']:
            # copy log in build dir and sync it to make it available to users
            shutil.copy2(log_file, sync_log_path)
            try:
                do_rsync(sync_log_path)
            except subprocess.CalledProcessError:
                pass
        notify_error('build', C.output)

    # copy log in build dir to make it available to users (in the site sync later)
    shutil.copy2(log_file, sync_log_path)

if not args.dry_run:
    syslog.syslog("Build of {}: start sync".format(name))
    try:
        if config['remote']:
            result = do_rsync('%s/' % build_dir)
        else:
            command = builder_info[config['builder']]['deploy_command']
            if command:
                result = subprocess.check_output(command).decode()
            else:
                result = "No deployment done: no Rsync settings provided and this builder has no reployment method defined"
        debug_print(result)
    except subprocess.CalledProcessError as C:
        notify_error('deploy', C.output)
    syslog.syslog("Build of {}: finish sync".format(name))
else:
    syslog.syslog("Build of {}: not syncing, dry-run".format(name))

status = {}
status['last_build_commit'] = current_commit
status['last_build'] = datetime.datetime.now().strftime("%s")
status['last_build_human'] = datetime.datetime.now().strftime("%c")
status['submodule_commits'] = current_submodule_commits

with open(status_file, 'w+') as f:
    f.write(yaml.dump(status, default_flow_style=False))
