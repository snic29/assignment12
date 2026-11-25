# Module 12

This FastAPI application has user accounts (JWT authentication) and per-user calculations (addition, subtraction, multiplication, division) stored in PostgreSQL.

**Docker Hub:** [stelni/assignment12](https://hub.docker.com/r/stelni/assignment12)

## Endpoints

Interactive API docs are served at `http://localhost:8000/docs` once the app is running.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | – | Health check |
| `POST` | `/users/register` | – | Create an account |
| `POST` | `/users/login` | – | Log in with username or email; returns access and refresh tokens |
| `POST` | `/users/token` | – | Form login used by the Swagger **Authorize** button |
| `GET` | `/users/me` | ✓ | Current user's profile |
| `GET` | `/calculations` | ✓ | Browse your calculations (newest first) |
| `POST` | `/calculations` | ✓ | Add a calculation |
| `GET` | `/calculations/{id}` | ✓ | Read one calculation |
| `PUT` | `/calculations/{id}` | ✓ | Replace a calculation (`type` and `inputs` required) |
| `PATCH` | `/calculations/{id}` | ✓ | Partially update a calculation (any of `type`, `inputs`) |
| `DELETE` | `/calculations/{id}` | ✓ | Delete a calculation |

Authenticated endpoints expect an `Authorization: Bearer <access_token>` header.

## Running the app

### With Docker Compose (from source)

```bash
docker compose up -d --build
```

This starts:

| Service | URL | Notes |
|---|---|---|
| API | http://localhost:8000 | Docs at `/docs` |
| PostgreSQL | `localhost:5432` | User `postgres`, password `postgres`. Databases `fastapi_db` (app) and `fastapi_test_db` (tests) |
| pgAdmin | http://localhost:5050 | Log in with `admin@example.com` / `admin`, then add a server with host `db`, user `postgres`, password `postgres` |

The tables are created automatically when the API starts.

### With the image from Docker Hub

The published image needs a PostgreSQL database. The simplest way is to reuse the Compose database:

```bash
docker pull stelni/assignment12:latest

# Start only the database (and pgAdmin) from docker-compose.yml
docker compose up -d db pgadmin

# Run the published image on the same Docker network
docker run --rm -p 8000:8000 \
  --network assignment12_app-network \
  -e DATABASE_URL=postgresql://postgres:postgres@db:5432/fastapi_db \
  -e JWT_SECRET_KEY=change-me-to-a-long-random-string \
  -e JWT_REFRESH_SECRET_KEY=change-me-to-another-long-random-string \
  stelni/assignment12:latest
```

The network name is `<folder-name>_app-network`. Check it with `docker network ls` if your folder isn't named `assignment12`. Stop the Compose `web` service first (`docker compose stop web`) or the two will both try to use port 8000.

## Running tests locally

### 1. Prerequisites

- Python 3.11
- A running PostgreSQL with a `fastapi_test_db` database. The Compose database creates it on first start:

  ```bash
  docker compose up -d db
  ```

  If your database volume was created before `init-db.sh` existed, create the test database by hand:

  ```bash
  docker compose exec db psql -U postgres -c "CREATE DATABASE fastapi_test_db;"
  ```

### 2. Set up a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the tests

```bash
pytest                              # everything, with a coverage report
pytest tests/unit                   # unit tests only
pytest tests/integration            # model, schema and in-process API tests
pytest tests/e2e                    # starts a real uvicorn server and calls it over HTTP
pytest tests/integration/test_calculation_endpoints.py -k lifecycle   # a single test
```

Every run needs the database, including `tests/unit`, because `tests/conftest.py` sets up the test tables before any test runs. Run with the virtual environment activated. The e2e tests start `uvicorn` as a subprocess and need it on your `PATH`.

Useful options:

| Option | Effect |
|---|---|
| `--preserve-db` | Keep the test tables after the run so you can inspect them in pgAdmin |
| `--run-slow` | Also run tests marked `@pytest.mark.slow` |
| `-x` | Stop at the first failure |
| `--no-cov` | Skip the coverage report (faster) |

Coverage is printed in the terminal, and an HTML report is written to `htmlcov/index.html`.

### Which database the tests use

The tests always run against `fastapi_test_db`, never the app's `fastapi_db`. The suite creates the tables at the start and drops them at the end, so pointing it at the app database would delete your data. To use a different test database, set `TEST_DATABASE_URL`:

```bash
TEST_DATABASE_URL=postgresql://user:password@localhost:5432/mytestdb pytest
```

### Test layout

| Folder | What it covers |
|---|---|
| `tests/unit` | Pure calculation operations |
| `tests/integration` | SQLAlchemy models, Pydantic schemas, auth helpers, and the API run in-process with FastAPI's `TestClient`. These tests check results directly in the database: users and hashed passwords after registration, `last_login` after login, and calculation rows after create, update and delete. They also check error status codes and messages for invalid data. |
| `tests/e2e` | The API running in a real uvicorn server, called over HTTP |

## CI/CD

The GitHub Actions workflow (`.github/workflows/test.yml`) runs the tests and a Trivy image scan. On pushes to `main` it then builds and pushes `stelni/assignment12:latest` and `stelni/assignment12:<commit-sha>` to [Docker Hub](https://hub.docker.com/r/stelni/assignment12).
