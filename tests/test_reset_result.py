"""Titulli (offline) testda o'quvchi natijasini tozalash — o'qilgan javoblar o'chadi, o'quvchi ro'yxatda qoladi."""

from test_api import SessionLocal, as_user, client, create_test, credits_of, register_teacher, reset_database


def test_titulli_testda_natijani_tozalash():
    from backend.database import Attempt, StudentAnswer, WrittenAnswer

    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")
    base = f"/api/teacher/tests/{created['id']}"

    before = credits_of()
    saved = client.post(f"{base}/scan/save", headers=as_user(555), json={
        "name": "Aliyev Vali", "answers": {"1": "B", "2": "C"},
        "written": {"4": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="},
    })
    assert saved.status_code == 200, saved.text
    assert credits_of() == before - 1
    with SessionLocal() as session:
        attempt_id = session.query(Attempt).filter_by(offline_name="Aliyev Vali").one().id
    detail = client.get(f"{base}/students/{attempt_id}/detail", headers=as_user(555)).json()
    assert detail["can_reset"] is True and detail["correct"] == 2

    # Boshqa o'qituvchi tozalay olmaydi
    register_teacher(556, "Boshqa", "Ustoz")
    assert client.post(f"{base}/students/{attempt_id}/reset", headers=as_user(556)).status_code == 404

    reset = client.post(f"{base}/students/{attempt_id}/reset", headers=as_user(555))
    assert reset.status_code == 200, reset.text
    with SessionLocal() as session:
        attempt = session.get(Attempt, attempt_id)
        assert attempt.status == "pending" and attempt.correct == 0 and attempt.finished_at is None
        assert session.query(StudentAnswer).filter_by(attempt_id=attempt_id).count() == 0
        assert session.query(WrittenAnswer).filter_by(attempt_id=attempt_id).count() == 0
    detail = client.get(f"{base}/students/{attempt_id}/detail", headers=as_user(555)).json()
    assert detail["can_reset"] is False  # natija yo'q — tozalanadigan narsa ham yo'q
    assert client.post(f"{base}/students/{attempt_id}/reset", headers=as_user(555)).status_code == 400

    # Qayta skanerlanadi — o'sha o'quvchiga yoziladi, token qayta yechiladi
    again = client.post(f"{base}/scan/save", headers=as_user(555),
                        json={"attempt_id": attempt_id, "answers": {"1": "A"}})
    assert again.status_code == 200, again.text
    assert credits_of() == before - 2
    with SessionLocal() as session:
        assert session.get(Attempt, attempt_id).status == "done"

    # Onlayn testda (o'quvchi o'zi ishlagan) — tozalash yo'q
    online = create_test()
    started = client.post(f"/api/tests/{online['code']}/start", json={}, headers=as_user(901)).json()
    client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(901))
    response = client.post(f"/api/teacher/tests/{online['id']}/students/{started['attempt_id']}/reset", headers=as_user(555))
    assert response.status_code == 404
