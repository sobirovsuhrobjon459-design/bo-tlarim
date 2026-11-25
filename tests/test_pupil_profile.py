"""O'quvchi kabineti: profil — xodimlar ko'radigan o'quvchi sahifasidagi ma'lumotlar bilan (faqat o'qish uchun)."""

from test_api import _set_name, as_user, client, main, reset_database


def test_oquvchi_kabinetida_profil_malumotlari():
    """Login-parol bilan kirgan o'quvchi o'z manzili, telefoni, holati, yotoqxonasi va to'lovini ko'radi."""

    reset_database()
    main._pupil_ip_failures.clear()
    _set_name(701, "Aziza", "Karimova")
    client.post("/api/schools", json={"subjects": ["Matematika"], "region": "Toshkent shahri",
                                      "district": "Chilonzor tumani", "name": "Ilm maktabi"}, headers=as_user(701))
    cid = client.post("/api/classes", json={"name": "7-A"}, headers=as_user(701)).json()["id"]
    client.put(f"/api/classes/{cid}/student-codes", json={"start": 1030, "end": 1040}, headers=as_user(701))
    client.post(f"/api/classes/{cid}/students", json={"full_name": "Aliyev Vali"}, headers=as_user(701))
    logins = client.get(f"/api/classes/{cid}/pupil-logins", headers=as_user(701)).json()
    vali = logins["students"][0]
    contact = client.put(f"/api/classes/{cid}/students/{vali['id']}/contact", headers=as_user(701), json={
        "region": "Toshkent shahri", "district": "Chilonzor tumani", "address": "Bunyodkor 5", "phone": "+998901234567",
        "grade": 7,
    })
    assert contact.status_code == 200, contact.text

    school_id = client.get("/api/pupil/schools", params={"q": "ilm"}).json()[0]["id"]
    session = client.post("/api/pupil/login", json={"school_id": school_id, "login": vali["login"],
                                                    "password": vali["password"]}).json()
    me = client.get("/api/pupil/me", headers={"X-Pupil-Session": session["token"]}).json()
    assert me["region"] == "Toshkent shahri" and me["district"] == "Chilonzor tumani" and me["address"] == "Bunyodkor 5"
    assert me["phone"] and me["grade"] == 7 and me["code"] == vali["code"]
    assert me["status"]["status"] == "at_school"  # holati (maktabda / kasal …) — xodim sahifasidagidek
    assert me["dorm"] is None  # yotoqxonaga joylashtirilmagan
    assert "payments" in me  # oylik to'lov (qarz) — sinfda to'lov yuritilsa
