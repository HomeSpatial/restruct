# SPDX-License-Identifier: Apache-2.0
"""Public packaging fails closed on newly introduced files and private inputs."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_public_release.py'
spec = importlib.util.spec_from_file_location('release_check', SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.names = ['README.md', 'release-files.json']
        self.assets = []
        (self.root / 'README.md').write_text('# Synthetic fixture\n')
        self.manifest()

    def manifest(self):
        (self.root / 'release-files.json').write_text(json.dumps(
            {'schema_version': 1, 'files': self.names, 'reviewed_assets': self.assets}))

    def test_only_allowlisted_files_are_public(self):
        (self.root / 'artifacts').mkdir()
        (self.root / 'artifacts/private.json').write_text('local data')
        (self.root / '.git').mkdir()
        (self.root / '.git/config').write_text('private remote')
        self.assertEqual(release.audit(self.root), sorted(self.names))

    def test_unlisted_file_and_credentials_are_rejected(self):
        secret = self.root / '.env'
        secret.write_text('TOKEN=local')
        with self.assertRaisesRegex(ValueError, 'Unlisted source file'):
            release.audit(self.root)
        secret.unlink()
        (self.root / 'README.md').write_text('gh' + 'p_' + 'a' * 36)
        with self.assertRaisesRegex(ValueError, 'credential-like'):
            release.audit(self.root)

    def test_private_case_content_and_broken_links_are_rejected(self):
        (self.root / 'README.md').write_text('private map' + '5 snapshot')
        with self.assertRaisesRegex(ValueError, 'private case'):
            release.audit(self.root)
        (self.root / 'README.md').write_text('[missing](docs/missing.md)')
        with self.assertRaisesRegex(ValueError, 'Broken'):
            release.audit(self.root)

    def test_additional_common_credential_formats_are_rejected(self):
        tokens = ['github' + '_pat_' + 'a' * 60, 'h' + 'f_' + 'a' * 34,
                  's' + 'k-' + 'a' * 48, 'AK' + 'IA' + 'A' * 16,
                  'AI' + 'za' + 'a' * 35, 'xox' + 'b-' + 'a' * 32,
                  'https://user:' + 'token-value' + '@example.invalid/repo',
                  '-----BEGIN ' + 'DSA PRIVATE KEY-----']
        for token in tokens:
            with self.subTest(prefix=token[:4]):
                (self.root / 'README.md').write_text(token)
                with self.assertRaisesRegex(ValueError, 'credential-like'):
                    release.audit(self.root)

    def test_manifest_cannot_escape_root_or_add_git_history(self):
        for path in ('../outside.txt', '.git/config', '/absolute.txt', 'C:/private.txt'):
            with self.subTest(path=path):
                self.names = ['README.md', 'release-files.json', path]
                self.manifest()
                with self.assertRaises(ValueError):
                    release.audit(self.root)

    def test_reviewed_image_requires_exact_bytes_and_provenance(self):
        from PIL import Image
        path = self.root / 'result.png'
        Image.new('RGB', (2, 2), 'red').save(path)
        self.names.append('result.png')
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'Binary file'):
            release.audit(self.root)
        self.assets = [{'path': 'result.png', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'provenance': 'Synthetic test image', 'notice': 'README.md'}]
        self.manifest()
        self.assertEqual(release.audit(self.root), sorted(self.names))
        Image.new('RGB', (2, 2), 'blue').save(path)
        with self.assertRaisesRegex(ValueError, 'checksum changed'):
            release.audit(self.root)
        self.assets[0]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        for field, value in [('notice', 'missing.md'), ('provenance', ''), ('path', '../outside.png')]:
            original = self.assets[0][field]
            self.assets[0][field] = value
            self.manifest()
            with self.assertRaisesRegex(ValueError, 'Invalid reviewed asset'):
                release.audit(self.root)
            self.assets[0][field] = original

    def test_reviewed_image_cannot_hide_text_credentials(self):
        path = self.root / 'result.jpg'
        raw = b'\xff\xd8\xff' + b'gh' + b'p_' + b'a' * 36
        path.write_bytes(raw)
        self.names.append('result.jpg')
        self.assets = [{'path': 'result.jpg', 'sha256': hashlib.sha256(raw).hexdigest(),
                        'provenance': 'Synthetic malformed image', 'notice': 'README.md'}]
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'credential-like'):
            release.audit(self.root)


if __name__ == '__main__':
    unittest.main()
