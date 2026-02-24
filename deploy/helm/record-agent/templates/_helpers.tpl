{{/*
Expand the chart name.
*/}}
{{- define "record-agent.name" -}}
{{- .Chart.Name }}
{{- end }}

{{/*
Create a fully qualified app name using the release name and chart name.
*/}}
{{- define "record-agent.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
