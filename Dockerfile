# ==========================================
# STAGE 1: Build Frontend Assets
# ==========================================
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies first (layer caching)
COPY package.json package-lock.json ./
RUN npm ci --no-audit --prefer-offline

# Copy source code and build production bundle
COPY . .
# Vite reemplaza VITE_* en build-time: con 'false' el desplegable demo (y las
# credenciales que referencia) no entran al bundle. Default false: una
# instalacion cliente no trae la demo; el compose de una maquina demo pasa true.
ARG VITE_DEMO_MODE=false
ENV VITE_DEMO_MODE=$VITE_DEMO_MODE
# API por mismo origen: nginx proxya /api/ al backend. Un valor absoluto (el
# default de desarrollo, http://127.0.0.1:8000/api/v1) quedaria horneado apuntando
# a la maquina del cliente, no al contenedor.
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# ==========================================
# STAGE 2: Production Web Server (Nginx)
# ==========================================
FROM nginx:alpine AS production

# Copy custom Nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy compiled SPA assets from builder
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
