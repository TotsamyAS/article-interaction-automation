import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .catalog import build_catalog
from .config import Settings
from .dataset import generate_dataset, dataset_digest
from .engine import execute
from .asr import ASR_ENGINE, ASR_ENGINE_VERSION


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self):
        connection = sqlite3.connect(self.settings.database_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        Path(self.settings.database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)")
            for path in sorted((Path(__file__).resolve().parent.parent / "migrations").glob("*.sql")):
                version = int(path.name.split("_", 1)[0])
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                applied = connection.execute("SELECT checksum FROM schema_migrations WHERE version = ?", (version,)).fetchone()
                if applied:
                    if applied["checksum"] != checksum:
                        raise RuntimeError("Изменена уже применённая миграция; создайте новую миграцию.")
                    continue
                connection.executescript(
                    f"BEGIN IMMEDIATE;\n{sql}\nINSERT INTO schema_migrations VALUES ({version}, '{checksum}');\nCOMMIT;"
                )
        finally:
            connection.close()
        records = generate_dataset(self.settings)
        catalog = build_catalog(self.settings.reference_date)
        truth = {key: execute(records, definition.query).model_dump(mode="json") for key, definition in catalog.items()}
        if any(not result["record_ids"] for result in truth.values()):
            raise RuntimeError("Набор данных содержит экспериментальную задачу с пустым эталоном.")
        protocol = self.settings.model_dump(
            mode="json",
            exclude={
                "host", "port", "database_path", "public_base_url", "login_redirect_path", "access_cookie_seconds",
                "llm_base_url", "llm_model", "llm_temperature", "llm_timeout_seconds", "m5_max_llm_steps",
                "asr_model", "asr_revision", "asr_model_path", "asr_device", "asr_compute_type", "asr_language",
                "asr_max_audio_bytes", "asr_max_audio_seconds",
            },
        )
        m4_asr = {
            "engine": ASR_ENGINE,
            "engine_version": ASR_ENGINE_VERSION,
            "model": self.settings.asr_model,
            "revision": self.settings.asr_revision,
            "device": self.settings.asr_device,
            "compute_type": self.settings.asr_compute_type,
            "language": self.settings.asr_language,
            "audio_limit_seconds": self.settings.asr_max_audio_seconds,
        }
        manifest = {"dataset_sha256": dataset_digest(records), "protocol": protocol, "protocol_version": 1,
                    "m4_asr": m4_asr,
                    "catalog_sha256": hashlib.sha256(encode({key: {"query": task.query.model_dump(mode="json"), "prompt": task.prompt}
                                                             for key, task in catalog.items()}).encode()).hexdigest()}
        with self.transaction() as connection:
            existing = connection.execute("SELECT manifest, records, ground_truth FROM dataset WHERE singleton = 1").fetchone()
            serialized_records = encode([record.model_dump(mode="json") for record in records])
            if existing:
                existing_manifest = json.loads(existing["manifest"])
                legacy_protocol = existing_manifest.get("protocol")
                if isinstance(legacy_protocol, dict):
                    legacy_protocol = dict(legacy_protocol)
                    for key in (
                        "llm_base_url", "llm_model", "llm_temperature", "llm_timeout_seconds", "m5_max_llm_steps",
                        "asr_model", "asr_revision", "asr_model_path", "asr_device", "asr_compute_type", "asr_language",
                        "asr_max_audio_bytes", "asr_max_audio_seconds",
                    ):
                        legacy_protocol.pop(key, None)
                    existing_manifest["protocol"] = legacy_protocol
                existing_manifest.pop("m3_prompt_sha256", None)
                if (existing_manifest != manifest or existing["records"] != serialized_records
                        or existing["ground_truth"] != encode(truth)):
                    raise RuntimeError("Конфигурация, данные или эталоны не совпадают с сохранённым протоколом. Используйте отдельный том для нового эксперимента.")
                if existing["manifest"] != encode(manifest):
                    connection.execute("UPDATE dataset SET manifest = ? WHERE singleton = 1", (encode(manifest),))
            else:
                connection.execute("INSERT INTO dataset VALUES (1, ?, ?, ?)",
                                   (encode(manifest), serialized_records, encode(truth)))
