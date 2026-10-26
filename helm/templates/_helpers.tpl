{{- define "voxos.name" -}}
{{- default .Release.Name .Values.nameOverride | trunc 48 | trimSuffix "-" -}}
{{- end -}}
{{- define "voxos.labels" -}}
app.kubernetes.io/name: voxos
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" | trimSuffix "_" | trimSuffix "." | quote }}
{{- end -}}
{{- define "voxos.image" -}}
{{- $image := index .root.Values.images .key -}}
{{- if $image.digest -}}
{{- printf "%s@%s" $image.repository $image.digest -}}
{{- else -}}
{{- printf "%s:%s" $image.repository (required (printf "images.%s.tag or digest is required" .key) $image.tag) -}}
{{- end -}}
{{- end -}}
{{- define "voxos.appEnv" -}}
{{- $env := dict "DATABASE_TLS_MODE" "driver-default" "PGSSLMODE" "disable" "PGCHANNELBINDING" "disable" "NEW_RELIC_ENABLED" "false" "VOICE_LIVEKIT_ENDPOINT_POLICY" "public_wss" "BROWSER_ACCOUNTS_STAGEHAND_BASE_URL" (printf "http://%s-browser-agent:3100" (include "voxos.name" .)) -}}
{{- if .Values.attachments.enabled }}
{{- $_ := set $env "ATTACHMENT_STORAGE_BACKEND" "local" -}}
{{- $_ := set $env "ATTACHMENT_LOCAL_PATH" "/var/lib/voxos/attachments" -}}
{{- end }}
{{- if .Values.application.firebaseAdminCredentialsSecret -}}
{{- $_ := set $env "GOOGLE_APPLICATION_CREDENTIALS" "/var/run/voxos-firebase/credentials.json" -}}
{{- end -}}
{{- $env = mergeOverwrite $env .Values.application.env -}}
{{- $_ := set $env "DB_HOST" .Values.database.host -}}
{{- $_ := set $env "DB_PORT" (.Values.database.port | toString) -}}
{{- range $key, $value := $env }}
- name: {{ $key }}
  value: {{ $value | quote }}
{{- end }}
{{- end -}}
{{- define "voxos.frontendSecurityHeaders" -}}
add_header X-Content-Type-Options nosniff always;
add_header Referrer-Policy no-referrer always;
add_header Cross-Origin-Opener-Policy same-origin-allow-popups always;
{{- with .Values.application.env.DEPLOYMENT_REVISION }}
add_header X-Voxos-Revision {{ . | quote }} always;
{{- end }}
add_header Content-Security-Policy "default-src 'self'; base-uri 'self'; connect-src 'self' https: wss:; form-action 'self'; frame-ancestors 'none'; frame-src 'self' https://*.firebaseapp.com https://accounts.google.com; img-src 'self' data: blob: https:; manifest-src 'self'; media-src 'self' blob:; object-src 'none'; script-src 'self' https://apis.google.com; style-src 'self' 'unsafe-inline'; worker-src 'self' blob:" always;
{{- end -}}
