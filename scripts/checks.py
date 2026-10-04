"""Run deterministic offline checks; full mode additionally uses Windows fonts."""
import argparse,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--windows',action='store_true',help='Include native Windows-font and crash-recovery checks')
args=parser.parse_args()
tests=['tests/test_smoke.py','tests/test_adapter_check.py','tests/test_adapters.py','tests/test_indexer.py','tests/test_source_languages.py','tests/test_source_settings.py','tests/test_search_sql.py','tests/test_backup.py']
if args.windows:
 if not Path('C:/Windows/Fonts/arial.ttf').exists():parser.error('Windows checks require installed Arial and Arial Bold')
 tests+=['tests/test_app.py','tests/test_original_checks.py','tests/test_archive_pipeline.py','tests/test_auto_repairs.py','tests/test_font_repairs.py','tests/test_font_exports.py','tests/test_import_queue.py','tests/test_download_queue.py','tests/test_source_zip.py','tests/test_import_recovery.py','tests/test_preview_recovery.py','tests/test_ots_recovery.py']
for test in tests:
 print('Checking '+test,flush=True)
 subprocess.run([sys.executable,test],cwd=root,check=True)
print('All offline checks passed',flush=True)
