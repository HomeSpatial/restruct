import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location("skill_sync",Path(__file__).resolve().parents[1]/"scripts/sync_skill.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class SkillSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/"source";self.target=self.root/"installed"
        (self.source/"agents").mkdir(parents=True)
        (self.source/"SKILL.md").write_text("new skill",encoding="utf-8")
        (self.source/"agents/openai.yaml").write_text("policy: keep\n",encoding="utf-8")

    def test_install_and_check_are_exact_and_check_never_writes(self):
        result=module.sync(self.source,self.target)
        self.assertTrue(result["passed"])
        (self.target/"SKILL.md").write_text("older",encoding="utf-8")
        result=module.sync(self.source,self.target,check=True)
        self.assertFalse(result["passed"])
        self.assertEqual((self.target/"SKILL.md").read_text(),"older")

    def test_update_requires_backup_and_preserves_previous_copy(self):
        module.sync(self.source,self.target)
        (self.target/"SKILL.md").write_text("old installed",encoding="utf-8")
        with self.assertRaises(ValueError):module.sync(self.source,self.target)
        backup=self.root/"backup"
        self.assertTrue(module.sync(self.source,self.target,backup)["passed"])
        self.assertEqual((backup/"SKILL.md").read_text(),"old installed")
        self.assertEqual(module.files(self.source),module.files(self.target))

    def test_custom_ui_policy_and_extra_files_are_preserved(self):
        module.sync(self.source,self.target)
        (self.target/"agents/openai.yaml").write_text("policy: customized\n",encoding="utf-8")
        with self.assertRaises(ValueError):module.sync(self.source,self.target,self.root/"backup")
        self.assertEqual((self.target/"agents/openai.yaml").read_text(),"policy: customized\n")
        (self.target/"agents/openai.yaml").write_text("policy: keep\n",encoding="utf-8")
        (self.target/"notes.txt").write_text("user owned",encoding="utf-8")
        with self.assertRaises(ValueError):module.sync(self.source,self.target,self.root/"backup")
        self.assertEqual((self.target/"notes.txt").read_text(),"user owned")

    def test_overlapping_or_existing_backup_is_rejected(self):
        with self.assertRaises(ValueError):module.sync(self.source,self.source/"child")
        module.sync(self.source,self.target)
        (self.target/"SKILL.md").write_text("old",encoding="utf-8")
        backup=self.root/"backup";backup.mkdir()
        with self.assertRaises(ValueError):module.sync(self.source,self.target,backup)
        self.assertEqual((self.target/"SKILL.md").read_text(),"old")


if __name__=="__main__":unittest.main()
