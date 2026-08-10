FROM python:3.12-slim

# pg_dump/pg_restore for the backup job (EXT-063), pinned to the same
# major version as the server in docker-compose.
#
# Debian trixie ships the 17 client, and dumping a 16 server with it emits
# SET transaction_timeout - a 17 setting the 16 server rejects on restore.
# It was harmless when measured (one ignored error, data intact), but a
# mismatched client is exactly the sort of thing that stays harmless until
# the restore you actually need. So the client comes from PGDG, pinned.
#
# Bump PG_MAJOR together with the postgres image in docker-compose.yml.
ENV PG_MAJOR=16
RUN apt-get update     && apt-get install --no-install-recommends -y curl ca-certificates gnupg     && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc         | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg     && . /etc/os-release     && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $VERSION_CODENAME-pgdg main"         > /etc/apt/sources.list.d/pgdg.list     && apt-get update     && apt-get install --no-install-recommends -y "postgresql-client-$PG_MAJOR"     && apt-get purge -y curl gnupg && apt-get autoremove -y     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
