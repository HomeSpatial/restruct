"""Host launcher contract: preserve sources and record the immutable image used."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location('container_run', Path(__file__).resolve().parents[1]/'containers/run.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class ContainerLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.weights = self.root/'weights'
        self.inputs = self.root/'input'
        self.weights.mkdir()
        self.inputs.mkdir()
        self.jobs = self.root/'jobs'
        self.record = self.root/'run.json'

    def argv(self, *extra):
        return ['run.py','--image','example:tag','--weights',str(self.weights),'--jobs',str(self.jobs),
                '--input','scene='+str(self.inputs),'--record',str(self.record),*extra,'--','prepare','/inputs/scene/scene.json','--out','/jobs/new']

    def test_records_immutable_image_and_read_only_bindings(self):
        process = mock.Mock(returncode=0)
        process.poll.return_value = 0
        with mock.patch('sys.argv',self.argv()), mock.patch.object(launcher.subprocess,'check_output',return_value='sha256:fixed\n'), \
             mock.patch.object(launcher.subprocess,'Popen',return_value=process) as popen, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                launcher.main()
        self.assertEqual(stopped.exception.code,0)
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index('--network')+1],'none')
        self.assertIn('sha256:fixed',command)
        self.assertNotIn('example:tag',command)
        self.assertIn('type=bind,source='+str(self.inputs.resolve())+',target=/inputs/scene,readonly',command)
        self.assertIn('type=bind,source='+str(self.weights.resolve())+',target=/weights,readonly',command)
        report = json.loads(self.record.read_text())
        self.assertTrue(report['passed'])
        self.assertEqual(report['image_id'],'sha256:fixed')
        self.assertEqual(report['read_only_inputs'],{'scene':str(self.inputs.resolve())})

    def test_rejects_jobs_overlapping_sources_before_running_docker(self):
        for path in (self.inputs, self.inputs/'child', self.root, self.weights/'child'):
            with self.subTest(path=path), mock.patch('sys.argv',self.argv()), mock.patch.object(launcher.subprocess,'check_output') as docker:
                argv = self.argv()
                argv[argv.index('--jobs')+1] = str(path)
                with mock.patch('sys.argv',argv), self.assertRaisesRegex(ValueError,'Jobs must be separate'):
                    launcher.main()
                docker.assert_not_called()

    def test_rejects_record_inside_source(self):
        argv = self.argv()
        argv[argv.index('--record')+1] = str(self.inputs/'run.json')
        with mock.patch('sys.argv',argv), self.assertRaisesRegex(ValueError,'records must be outside'):
            launcher.main()
        self.assertFalse(self.jobs.exists())

    def test_preserves_existing_record(self):
        self.record.write_text('original')
        with mock.patch('sys.argv',self.argv()), self.assertRaisesRegex(ValueError,'new record/log paths'):
            launcher.main()
        self.assertEqual(self.record.read_text(),'original')


if __name__ == '__main__':
    unittest.main()
