"""Run the image from Windows or Linux with read-only inputs/weights and an isolated job root."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--input', action='append', default=[], metavar='NAME=HOST_PATH')
    parser.add_argument('--weights', required=True, type=Path)
    parser.add_argument('--jobs', required=True, type=Path)
    parser.add_argument('--record', required=True, type=Path)
    parser.add_argument('--entrypoint')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or args.record.exists() or args.record.with_suffix('.log').exists():
        raise ValueError('Supply a command and new record/log paths')
    if not args.weights.is_dir():
        raise ValueError('Weights root is unavailable')
    inputs = {}
    for binding in args.input:
        key, value = binding.split('=',1)
        if not re.fullmatch('[A-Za-z0-9_-]+',key) or key in inputs or not Path(value).exists():
            raise ValueError('Invalid or duplicate input binding: '+binding)
        inputs[key] = str(Path(value).resolve())
    sources = [args.weights.resolve(), *(Path(value) for value in inputs.values())]
    jobs = args.jobs.resolve()
    for source in sources:
        if jobs == source or source in jobs.parents or jobs in source.parents:
            raise ValueError('Jobs must be separate from read-only inputs/weights')
        for output in (args.record.resolve(), args.record.with_suffix('.log').resolve()):
            if output == source or source in output.parents:
                raise ValueError('Run records must be outside read-only inputs/weights')
    args.jobs.mkdir(parents=True, exist_ok=True)
    name = 'indoor-recon-'+uuid.uuid4().hex[:12]
    resolved = subprocess.check_output(['docker','image','inspect',args.image,'--format','{{.Id}}'],text=True).strip()
    docker = ['docker','run','--rm','--name',name,'--gpus','all','--network','none','--shm-size','4g',
              '--mount',f'type=bind,source={args.weights.resolve()},target=/weights,readonly',
              '--mount',f'type=bind,source={args.jobs.resolve()},target=/jobs']
    for key, source in inputs.items():
        docker.extend(['--mount',f'type=bind,source={source},target=/inputs/{key},readonly'])
    if args.entrypoint:
        docker.extend(['--entrypoint',args.entrypoint])
    docker.extend([resolved,*command])
    args.record.parent.mkdir(parents=True,exist_ok=True)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    clock = time.monotonic()
    samples = []
    with args.record.with_suffix('.log').open('w',encoding='utf-8') as log:
        process = subprocess.Popen(docker,stdout=log,stderr=subprocess.STDOUT)
        while process.poll() is None:
            time.sleep(5)
            if process.poll() is not None:
                break
            stat = subprocess.run(['docker','stats','--no-stream','--format','{{json .}}',name],capture_output=True,text=True)
            if stat.returncode == 0 and stat.stdout.strip():
                samples.append(dict(json.loads(stat.stdout),elapsed_seconds=time.monotonic()-clock))
    report = {'passed':process.returncode==0,'returncode':process.returncode,'image':args.image,'image_id':resolved,
              'started_utc':started,'seconds':time.monotonic()-clock,'command':docker,
              'read_only_inputs':inputs,'read_only_weights':str(args.weights.resolve()),'jobs':str(args.jobs.resolve()),
              'container_stats_samples':samples,'log':str(args.record.with_suffix('.log').resolve())}
    args.record.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('container_stats_samples','command')}))
    raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
