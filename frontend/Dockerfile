# Build stage
FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json ./
# npm install (not ci): the committed lock is generated on macOS and omits the
# linux-only optional deps (@emnapi/*) that the SWC/lightningcss native bindings
# need, so a strict `npm ci` fails in the linux build image.
RUN npm install --no-audit --no-fund

COPY . .
# NEXT_PUBLIC_* is inlined at build time; the browser reaches the API on the host port.
ARG NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
RUN npm run build

# Runtime stage
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=builder /app/package.json /app/package-lock.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/next.config.ts ./
COPY --from=builder /app/app ./app

EXPOSE 3000

CMD ["npx", "next", "start", "-p", "3000"]
