# RBQA – Rishi Bharadwaj Quantum Academy

Flask + SQLite backend with the original website as the frontend and an admin panel.

## Run locally

    pip install -r requirements.txt
    python app.py

- Website: http://localhost:5000
- Admin:   http://localhost:5000/admin  (default login `admin` / `admin123` – change it under Settings)

First start creates `rbqa.db` and seeds the 5 courses and 2 workshops from the demo.

## What is connected
| Frontend | Backend |
|---|---|
| Courses grid + "View Curriculum" popup | `GET /api/courses` |
| Workshops list + "Register Interest" popup | `GET /api/workshops`, `POST /api/workshops/<id>/register` |
| Contact form | `POST /api/contact` |
| Admin panel | messages, registrations (+CSV export), courses & workshops CRUD, password change |

Anything you add/edit/hide in the admin panel shows on the public site immediately.

## Options (environment variables)
`PORT` (default 5000), `RBQA_ADMIN_USER`, `RBQA_ADMIN_PASS` (used on first run only), `RBQA_DB`, `RBQA_SECRET`.
Example: `RBQA_ADMIN_PASS='MyStrongPass1' python app.py`

## Before going public
Use a production server (e.g. `gunicorn app:app`) behind HTTPS and change the admin password.
Not wired yet because the demo had placeholders: WhatsApp number, social links, email sending (messages are stored in the admin panel, not emailed).
