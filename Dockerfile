# =============================================================================
# Frontend de Dat.ia - build de Vite + nginx sin privilegios en 8080.
#
# El contexto de build es la raiz del repo. `VITE_*` se hornea en build-time:
# la API va por ruta relativa y nginx la proxya al backend.
# =============================================================================
FROM node:24.22.1-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --no-audit --prefer-offline

COPY . .

# Sin demo: una instalacion cliente no lleva cuentas ni dataset de demostracion.
ARG VITE_DEMO_MODE=false
ENV VITE_DEMO_MODE=$VITE_DEMO_MODE
# Mismo origen: nginx proxya /api/ al backend. Una URL absoluta quedaria
# horneada apuntando a la maquina del cliente, no al contenedor.
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build


FROM nginxinc/nginx-unprivileged:1.31.7-alpine AS runtime

# Plantilla, no config final: el entrypoint de nginx hace envsubst de
# ${TRUSTED_PROXY_CIDR} al arrancar. El default no confia en ningun proxy; el
# compose de produccion lo fija al CIDR de Caddy o del LB de la empresa.
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV TRUSTED_PROXY_CIDR=127.0.0.1

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
