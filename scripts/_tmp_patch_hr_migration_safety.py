from pathlib import Path

path = Path("google_health_viewer/workers.py")
text = path.read_text()
old_pre = '''                        heart_rate_bounds = self.store.data_type_date_bounds("heart-rate")\n                        self.store.delete_records_by_kind("heart-rate", "data_point")\n                        self.store.reset_sync_ranges("heart-rate")\n'''
new_pre = '''                        heart_rate_bounds = self.store.data_type_date_bounds("heart-rate")\n                        self.store.reset_sync_ranges("heart-rate")\n'''
if old_pre in text:
    text = text.replace(old_pre, new_pre, 1)

old_post = '''                    if needs_heart_rate_migration:\n                        self.store.set_app_marker(HEART_RATE_STORAGE_VERSION)\n'''
new_post = '''                    if needs_heart_rate_migration:\n                        # Keep legacy raw samples until every requested rollup page has\n                        # been persisted successfully. A failed network/API migration\n                        # therefore remains retryable without losing local history.\n                        self.store.delete_records_by_kind("heart-rate", "data_point")\n                        self.store.set_app_marker(HEART_RATE_STORAGE_VERSION)\n'''
if new_post not in text:
    if old_post not in text:
        raise AssertionError("Heart-rate migration completion anchor missing")
    text = text.replace(old_post, new_post, 1)

path.write_text(text)
