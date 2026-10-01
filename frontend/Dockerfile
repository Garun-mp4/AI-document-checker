FROM node:22-alpine AS build
WORKDIR /app
ARG APP_BUILD_ID=unverified
ARG APP_BUILD_COMMIT=unknown
ARG APP_BUILD_TIME=unknown
ENV APP_BUILD_ID=${APP_BUILD_ID} \
    APP_BUILD_COMMIT=${APP_BUILD_COMMIT} \
    APP_BUILD_TIME=${APP_BUILD_TIME}
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
