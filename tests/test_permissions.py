import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


os.environ.setdefault("PAYCLEAR_ENV", "production")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import database  # noqa: E402
from main import app  # noqa: E402
from models import Base, CheckReport, MonthlySubmission, Team, User, Worker, WorkerBankInfo, WorkerContract  # noqa: E402
from routers.auth import get_password_hash  # noqa: E402


class PermissionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{os.path.join(self.tmpdir.name, 'test.db')}",
            connect_args={"check_same_thread": False},
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[database.get_db] = override_get_db
        self.client = TestClient(app)
        self._seed()

    def tearDown(self):
        app.dependency_overrides.clear()
        self.tmpdir.cleanup()

    def _seed(self):
        with self.SessionLocal() as db:
            team_a = Team(name="A队")
            team_b = Team(name="B队")
            db.add_all([team_a, team_b])
            db.flush()

            users = [
                User(username="admin", password_hash=get_password_hash("AdminPass12345"), role="admin"),
                User(username="operator", password_hash=get_password_hash("OperatorPass12345"), role="operator"),
                User(username="leader_a", password_hash=get_password_hash("LeaderPass12345"), role="team_leader", team_id=team_a.id),
                User(username="leader_b", password_hash=get_password_hash("LeaderPass12345"), role="team_leader", team_id=team_b.id),
            ]
            db.add_all(users)
            db.flush()

            worker_a = Worker(name="张三", id_card="110101199001011234")
            worker_b = Worker(name="李四", id_card="110101199001015678")
            db.add_all([worker_a, worker_b])
            db.flush()

            db.add_all([
                WorkerBankInfo(worker_id=worker_a.id, team_id=team_a.id, status="confirmed"),
                WorkerBankInfo(worker_id=worker_b.id, team_id=team_b.id, status="confirmed"),
                WorkerContract(worker_id=worker_a.id, id_card=worker_a.id_card, name=worker_a.name, original_filename="a.pdf"),
                WorkerContract(worker_id=worker_b.id, id_card=worker_b.id_card, name=worker_b.name, original_filename="b.pdf"),
            ])

            submission = MonthlySubmission(team_id=team_a.id, year=2026, month=7, submitted_by=users[0].id, status="done")
            db.add(submission)
            db.flush()
            report_data = {
                "issues": [{
                    "severity": "error",
                    "issue_type": "cross_table",
                    "worker_name": "张三",
                    "id_card": worker_a.id_card,
                    "field": "bank_card",
                    "description": "测试问题",
                }]
            }
            db.add(CheckReport(
                submission_id=submission.id,
                generated_at=datetime.utcnow(),
                report_data=json.dumps(report_data, ensure_ascii=False),
                total_workers=1,
                error_count=1,
                warning_count=0,
            ))
            db.commit()

    def _login_headers(self, username, password):
        response = self.client.post("/api/auth/login", data={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def test_operator_cannot_export_excel_report(self):
        headers = self._login_headers("operator", "OperatorPass12345")

        response = self.client.get("/api/reports/1/export", headers=headers)

        self.assertEqual(response.status_code, 403)

    def test_team_leader_only_sees_own_team_contracts(self):
        headers = self._login_headers("leader_a", "LeaderPass12345")

        response = self.client.get("/api/contracts", headers=headers)

        self.assertEqual(response.status_code, 200, response.text)
        names = {item["name"] for item in response.json()}
        self.assertEqual(names, {"张三"})

    def test_contract_upload_rejects_more_than_twenty_files(self):
        headers = self._login_headers("admin", "AdminPass12345")
        files = [
            ("files", (f"contract-{index}.pdf", b"%PDF-1.4", "application/pdf"))
            for index in range(21)
        ]

        response = self.client.post("/api/contracts/upload", headers=headers, files=files)

        self.assertEqual(response.status_code, 400)
        self.assertIn("最多上传 20", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
