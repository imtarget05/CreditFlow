# CreditFlow web — production image (node build -> nginx serve).
# API đi cùng origin qua /api/ -> http://api:8080/ (compose network),
# nên KHÔNG cần CORS và KHÔNG dùng Vite dev proxy ở production.
FROM node:20-alpine AS build
WORKDIR /web
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /web/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
