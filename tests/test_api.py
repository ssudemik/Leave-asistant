def leave(client, employee_id, start, end, leave_type="annual"):
    return client.post(
        "/leave-requests",
        json={
            "employee_id": employee_id,
            "leave_type": leave_type,
            "start_date": start,
            "end_date": end,
        },
    )


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_duplicate_email_is_rejected(client, employee):
    response = client.post("/employees", json={"full_name": "Someone", "email": "ada@example.com"})
    assert response.status_code == 409


def test_create_request_counts_only_working_days(client, employee):
    # Fri 2026-12-11 to Tue 2026-12-15 -> Fri, Mon, Tue = 3 working days
    response = leave(client, employee["id"], "2026-12-11", "2026-12-15")
    assert response.status_code == 201
    body = response.json()
    assert body["working_days"] == 3
    assert body["status"] == "pending"


def test_end_before_start_is_invalid(client, employee):
    assert leave(client, employee["id"], "2026-12-15", "2026-12-11").status_code == 422


def test_weekend_only_request_is_rejected(client, employee):
    response = leave(client, employee["id"], "2026-12-12", "2026-12-13")
    assert response.status_code == 422
    assert "no working days" in response.json()["detail"]


def test_overlapping_requests_conflict(client, employee):
    assert leave(client, employee["id"], "2026-12-14", "2026-12-16").status_code == 201
    response = leave(client, employee["id"], "2026-12-16", "2026-12-18")
    assert response.status_code == 409


def test_rejected_request_frees_the_dates(client, employee):
    first = leave(client, employee["id"], "2026-12-14", "2026-12-16").json()
    client.post(f"/leave-requests/{first['id']}/reject")
    assert leave(client, employee["id"], "2026-12-14", "2026-12-16").status_code == 201


def test_balance_includes_pending_and_blocks_overspending(client, employee):
    # 10 days entitlement: 5 approved + 4 pending = 9, so a 2-day request must fail
    first = leave(client, employee["id"], "2026-12-07", "2026-12-11").json()
    client.post(f"/leave-requests/{first['id']}/approve")
    leave(client, employee["id"], "2026-12-14", "2026-12-17")

    balance = client.get(f"/employees/{employee['id']}/balance", params={"year": 2026}).json()
    assert balance == {
        "employee_id": employee["id"],
        "year": 2026,
        "entitlement": 10,
        "approved": 5,
        "pending": 4,
        "remaining": 1,
    }
    response = leave(client, employee["id"], "2026-12-21", "2026-12-22")
    assert response.status_code == 422
    assert "Not enough annual leave" in response.json()["detail"]


def test_sick_leave_does_not_use_annual_balance(client, employee):
    assert leave(client, employee["id"], "2026-12-01", "2026-12-31", "sick").status_code == 201
    balance = client.get(f"/employees/{employee['id']}/balance", params={"year": 2026}).json()
    assert balance["remaining"] == 10


def test_cannot_decide_twice(client, employee):
    request = leave(client, employee["id"], "2026-12-14", "2026-12-14").json()
    assert client.post(f"/leave-requests/{request['id']}/approve").status_code == 200
    assert client.post(f"/leave-requests/{request['id']}/reject").status_code == 409


def test_unknown_employee_returns_404(client):
    assert leave(client, 999, "2026-12-14", "2026-12-14").status_code == 404


def test_request_across_years_is_rejected(client, employee):
    assert leave(client, employee["id"], "2026-12-31", "2027-01-04").status_code == 422


def test_create_from_text(client, employee):
    response = client.post(
        "/leave-requests/from-text",
        json={
            "employee_id": employee["id"],
            "text": "I'd like next Monday to Wednesday off for a family trip",
            "today": "2026-11-09",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["start_date"], body["end_date"]) == ("2026-11-16", "2026-11-18")
    assert body["reason"] == "a family trip"


def test_parse_returns_422_when_no_date(client):
    response = client.post("/leave-requests/parse", json={"text": "I need some rest"})
    assert response.status_code == 422
