import tempfile
import unittest
from collections import Counter
from pathlib import Path

from enemybase_editor import Document, find_references


def record(number='1', name='乌力'):
    fields = [''] * 56
    fields[0], fields[6], fields[8], fields[36] = name, number, '4.50', '100250'
    return ','.join(fields)


class DocumentTests(unittest.TestCase):
    def test_roundtrip_encodings_comments_and_no_final_newline(self):
        for encoding in ('gbk', 'utf-8-sig', 'utf-8'):
            data = ('# 注释\r\n\r\n' + record()).encode(encoding)
            doc = Document(data, encoding)
            self.assertEqual(doc.bytes(), data)
            self.assertEqual(len(doc.records()), 1)
            self.assertFalse(doc.issues())

    def test_edit_preserves_empty_and_extension_fields(self):
        doc = Document(record().encode('gbk'))
        doc.records()[0].fields[55] = '自定义值'
        doc.records()[0].fields[0] = '测试'
        result = Document(doc.bytes())
        self.assertEqual(result.records()[0].fields[55], '自定义值')
        self.assertEqual(result.records()[0].fields[7], '')

    def test_invalid_and_duplicate_records(self):
        doc = Document((record() + '\n' + record() + '\n坏记录').encode('gbk'))
        self.assertEqual(len(doc.issues()), 3)

    def test_backup_and_external_change_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'enemybase.txt'
            original = record().encode('gbk')
            path.write_bytes(original)
            doc = Document(original)
            doc.records()[0].fields[0] = '修改'
            doc.save(path, doc.digest)
            backup = path.with_name(path.name + '.bak')
            self.assertEqual(backup.read_bytes(), original)
            first_saved = path.read_bytes()
            self.assertNotEqual(first_saved, original)
            doc.records()[0].fields[0] = '再次修改'
            doc.save(path, doc.digest)
            self.assertEqual(backup.read_bytes(), first_saved)
            self.assertEqual(len(list(Path(directory).glob('*.bak'))), 1)
            path.write_bytes(b'external change')
            with self.assertRaises(ValueError):
                doc.save(path, doc.digest)
            self.assertEqual(path.read_bytes(), b'external change')

    def test_real_file_roundtrip(self):
        path = Path(r'C:\Work\SA\sa-server\2.5\gmsv\data\enemybase.txt')
        if not path.exists():
            self.skipTest('Local fixture unavailable')
        data = path.read_bytes()
        doc = Document(data)
        self.assertEqual(doc.bytes(), data)
        self.assertGreater(len(doc.records()), 0)
        self.assertFalse(doc.issues())

    def test_reference_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'enemybase.txt'
            base.write_text(record('1500'), encoding='gbk')
            enemy = [''] * 34
            enemy[0], enemy[3], enemy[4], enemy[5], enemy[6] = '实例', '2000', '1500', '1', '3'
            (root / 'enemy.txt').write_text(','.join(enemy), encoding='gbk')
            group = [''] * 24
            group[0], group[1], group[2], group[3], group[4], group[14] = '测试组', '3000', '-1', '-1', '2000', '100'
            (root / 'group1.txt').write_text(','.join(group), encoding='gbk')
            encount = [''] * 33
            encount[:10] = ['1', '100', '1', '2', '3', '4', '1', '5', '2', '10']
            encount[10], encount[20] = '3000', '100'
            (root / 'encount.txt').write_text(','.join(encount), encoding='gbk')
            npc = root / 'npc'
            npc.mkdir()
            (npc / 'give.arg').write_text('ACTION:AddPet:2000\nPETTEMPNO:1500\n', encoding='gbk')
            references, warnings = find_references(base, '1500')
            counts = Counter(reference.kind for reference in references)
            self.assertEqual(counts, {'实例': 1, '遇敌组': 1, '地图遇敌': 1, 'NPC数据': 2})
            self.assertFalse(warnings)


if __name__ == '__main__':
    unittest.main()
