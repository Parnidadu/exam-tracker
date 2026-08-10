# The public-facing container: the built SPA, plus Caddy terminating TLS
# and proxying everything else to Django.
#
# One artefact rather than "build the frontend, then copy it into a volume
# a proxy reads": a volume that has to be populated before the proxy
# starts is a deploy step that can be forgotten, and it fails by serving
# the previous release rather than by failing.

FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /app/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile
