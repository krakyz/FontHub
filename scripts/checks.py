"""Run deterministic offline checks; full mode additionally uses Windows fonts."""
import argparse,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--windows',action='store_true',help='Include native Windows-font and crash-recovery checks')
args=parser.parse_args()
tests=['tests/test_smoke.py','test_adapter_check.py','test_adapters.py','test_indexer.py','test_source_languages.py','test_source_settings.py','test_search_sql.py','test_backup.py']
if args.windows:
 if not Path('C:/Windows/Fonts/arial.ttf').exists():parser.error('Windows checks require installed Arial and Arial Bold')
 tests+=['test_app.py','test_original_checks.py','test_archive_pipeline.py','test_auto_repairs.py','test_font_repairs.py','test_font_exports.py','test_import_queue.py','test_download_queue.py','test_source_zip.py','test_import_recovery.py','test_preview_recovery.py','test_ots_recovery.py']
for test in tests:
 print('Checking '+test,flush=True)
 subprocess.run([sys.executable,test],cwd=root,check=True)
print('All offline checks passed',flush=True)
