import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / 'payload'
PROVIDERS = ('tesser', 'modal', 'e2b', 'namespace')
TTL = 360


def command(argv, *, timeout=30, cwd=ROOT, capture=True):
    result = subprocess.run(
        argv, cwd=cwd, timeout=timeout, text=True, stdin=subprocess.DEVNULL,
        capture_output=capture,
    )
    if result.returncode:
        message = (result.stderr or result.stdout or '').strip()[-1600:]
        raise RuntimeError(f'{Path(argv[0]).name} exited {result.returncode}: {message}')
    return result.stdout or ''


def cli(name):
    binary = shutil.which(name)
    if name == 'tesser' and not binary:
        candidate = Path.home() / '.tesser/bin/tesser'
        if candidate.is_file():
            binary = str(candidate)
    if not binary:
        raise RuntimeError(f'Install the {name} CLI first')
    return binary


def e2b_key():
    if key := os.environ.get('E2B_API_KEY'):
        return key
    try:
        config = json.loads((Path.home() / '.e2b/config.json').read_text())
    except (OSError, ValueError):
        config = {}
    key = config.get('projectApiKey') if isinstance(config, dict) else None
    if isinstance(key, str) and key:
        return key
    raise RuntimeError('Run e2b auth login or set E2B_API_KEY in the client environment')


def _patch_e2b_process_method_info():
    # E2B 2.63.0's generated process client omits the stream type required by connectrpc 0.13.
    from connectrpc.method import MethodInfo, StreamType
    from e2b.envd.process import process_connect

    if process_connect.MethodInfo is not MethodInfo:
        return
    stream_types = {
        'unary': StreamType.UNARY,
        'client_streaming': StreamType.CLIENT_STREAM,
        'server_streaming': StreamType.SERVER_STREAM,
        'bidi_streaming': StreamType.BIDI_STREAM,
    }
    methods = {
        method.name: stream_types[method.method_kind]
        for method in process_connect.ProcessSync.desc().methods
    }

    def method_info(*, name, **kwargs):
        return MethodInfo(name=name, stream_type=methods[name], **kwargs)

    process_connect.MethodInfo = method_info


class Sandbox:
    def __init__(self, provider, settings, name, *, smoke=False, existing=None):
        self.provider, self.settings, self.name = provider, settings, name
        self.existing = existing
        self.id = existing
        self.handle = None
        self.before = set()
        self.smoke = smoke
        if provider == 'tesser':
            self.binary = cli('tesser')
            self.version = command([self.binary, '--version']).strip()
            command(['git', 'rev-parse', '--verify', 'HEAD'])
            self.before = {box['boxId'] for box in self.tesser_boxes()}
        elif provider == 'namespace':
            self.binary = cli('devbox')
            self.version = command([self.binary, 'version']).strip()
        elif provider == 'modal':
            import modal

            self.modal = modal
            self.version = modal.__version__
            self.app = modal.App.lookup(settings['app'], create_if_missing=smoke)
            self.image = (modal.Image.debian_slim() if smoke else modal.Image.from_id(settings['image']))
        else:
            import e2b
            from importlib.metadata import version

            _patch_e2b_process_method_info()
            self.e2b_api_key = e2b_key()
            self.e2b = e2b
            self.version = version('e2b')

    def tesser_boxes(self):
        return json.loads(command([self.binary, 'ls', '--json']))

    def launch(self, timeout):
        if self.existing:
            return
        settings = self.settings
        if self.provider == 'tesser':
            args = [self.binary, 'make', '--size', settings['size']]
            args += ['--no-snapshot'] if self.smoke else ['--snapshot', settings['snapshot']]
            box = command(args, timeout=timeout).strip()
            if not re.fullmatch(r'box_[0-9a-f]+', box) or box in self.before:
                raise RuntimeError('Tesser did not return a new box ID')
            self.id = box
        elif self.provider == 'namespace':
            args = [self.binary, 'create', '--name', self.name, '--platform', 'linux/amd64',
                    '--size', settings['size'], '--ephemeral', '--no_checkout',
                    '--access_mode', 'private', '--auto_stop_idle_timeout', '5m',
                    '--image', 'builtin:default' if self.smoke else settings['image']]
            if site := settings.get('site'):
                args += ['--site', site]
            command(args, timeout=timeout)
            self.id = self.name
        elif self.provider == 'modal':
            options = {'region': settings['region']} if settings.get('region') else {}
            self.handle = self.modal.Sandbox.create(
                app=self.app, image=self.image, name=self.name, runtime='gvisor',
                cpu=(1.0, 1.0), memory=(4096, 4096), timeout=TTL, **options,
            )
            self.id = self.handle.object_id
        else:
            self.handle = self.e2b.Sandbox.create(
                **({} if self.smoke else {'template': settings['template']}),
                metadata={'benchpress_trial': self.name}, timeout=TTL, request_timeout=timeout,
                api_key=self.e2b_api_key,
            )
            self.id = self.handle.sandbox_id

    def exec(self, argv, timeout):
        if self.provider == 'tesser':
            # The smoke test checks checkout sync. Benchmarks use the snapshot payload.
            subcommand = 'exec' if self.smoke else 'ssh'
            return command([self.binary, subcommand, self.id, '--', *argv], timeout=timeout)
        if self.provider == 'namespace':
            return command([self.binary, 'exec', self.id, '--', *argv], timeout=timeout)
        if self.provider == 'modal':
            proc = self.handle.exec(*argv, timeout=max(1, math.ceil(timeout)))
            output = proc.stdout.read()
            if proc.wait():
                raise RuntimeError(f'Modal command exited {proc.returncode}: {proc.stderr.read()[-1600:]}')
            return output
        seconds = max(1, math.ceil(timeout))
        result = self.handle.commands.run(shlex.join(argv), timeout=seconds, request_timeout=seconds)
        return result.stdout

    def payload(self, script, *args):
        if self.provider == 'tesser':
            script_command = 'script=$1; shift; exec "$HOME/.benchpress/node" "$HOME/.benchpress/$script" "$@"'
            return ['sh', '-c', script_command, 'sh', script, *args]
        return ['/opt/benchpress/node', f'/opt/benchpress/{script}', *args]

    def placement(self):
        if self.provider == 'namespace':
            boxes = json.loads(command([self.binary, 'list', '--output', 'json'], timeout=15))
            box = next(b for b in boxes if b['name'] == self.id)
            return {'site': box.get('site'), 'shape': box.get('instance_shape')}
        if self.provider == 'tesser':
            box = json.loads(command([self.binary, 'status', self.id, '--json'], timeout=15))
            return {key: box[key] for key in ('region', 'site', 'size', 'fleet') if key in box}
        if self.provider == 'e2b':
            info = self.handle.get_info(request_timeout=10)
            return {'vcpu': info.cpu_count, 'memory_mib': info.memory_mb, 'domain': info.sandbox_domain}
        return {}

    def close(self):
        if self.existing:
            return 'existing_box_kept'
        if self.provider == 'tesser':
            if not self.id:
                boxes = [b['boxId'] for b in self.tesser_boxes() if b['boxId'] not in self.before
                         and not b.get('service') and any(w.get('root') == str(ROOT)
                         for w in b.get('worktrees', []))]
                if len(boxes) != 1:
                    return 'creation_unconfirmed'
                self.id = boxes[0]
            command([self.binary, 'rm', self.id])
        elif self.provider == 'namespace':
            try:
                command([self.binary, 'expire', self.name, '--force'])
            except RuntimeError as error:
                if 'code = NotFound' in str(error):
                    return 'creation_unconfirmed'
                raise
            self.id = self.name
        elif self.provider == 'modal':
            if not self.handle:
                try:
                    self.handle = self.modal.Sandbox.from_name(self.settings['app'], self.name)
                except self.modal.exception.NotFoundError:
                    return 'creation_unconfirmed'
                self.id = self.handle.object_id
            self.handle.terminate(wait=True)
        else:
            if not self.handle:
                from e2b.sandbox.sandbox_api import SandboxQuery

                matches = self.e2b.Sandbox.list(
                    query=SandboxQuery(metadata={'benchpress_trial': self.name}), limit=2,
                    request_timeout=15, api_key=self.e2b_api_key,
                ).next_items()
                if len(matches) != 1:
                    return 'creation_unconfirmed'
                self.id = matches[0].sandbox_id
            self.e2b.Sandbox.kill(self.id, request_timeout=15, api_key=self.e2b_api_key)
        return 'removed'


def prepare(provider, settings, name):
    if provider == 'tesser':
        line = command([cli('tesser'), 'snapshot', 'create', '--wait'], timeout=1200).strip().splitlines()[-1]
        return {'snapshot': line.split()[0]}
    if provider == 'namespace':
        command([cli('devbox'), 'image', 'build', str(PAYLOAD), '--name', name, '--user', 'root'],
                timeout=1200, capture=False)
        return {'image': name}
    if provider == 'modal':
        import modal

        app = modal.App.lookup(settings['app'], create_if_missing=True)
        image = modal.Image.from_registry('python:3.15.2-slim-trixie').apt_install(
            'curl', 'xz-utils', 'libatomic1',
        )
        image = image.add_local_dir(PAYLOAD, '/tmp/benchpress-payload', copy=True).run_commands(
            'sh /tmp/benchpress-payload/prepare.sh /opt/benchpress && rm -rf /tmp/benchpress-payload',
        )
        image = image.build(app)
        return {'image': image.object_id}
    from e2b import Template

    api_key = e2b_key()
    files = ['prepare.sh', 'fixture.mjs', 'run.mjs', 'machine.mjs']
    template = Template(file_context_path=PAYLOAD).from_image('node:26.13.1-trixie-slim').copy(
        files, '/tmp/benchpress-payload/', user='root',
    ).run_cmd('sh /tmp/benchpress-payload/prepare.sh /opt/benchpress && rm -rf /tmp/benchpress-payload',
              user='root')
    build = Template.build(template, name=name, cpu_count=2, memory_mb=4096, api_key=api_key)
    return {'template': build.template_id}
