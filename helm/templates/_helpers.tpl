{{/*
Namespaced resource prefix.
*/}}
{{- define "oracle-database-operator.name" -}}
oracle-database-operator
{{- end }}

{{/* Operator resources can use an existing namespace outside the Helm release. */}}
{{- define "oracle-database-operator.namespace" -}}
{{- default .Release.Namespace .Values.namespaceOverride -}}
{{- end }}

{{/* Cluster resources include the release namespace and name in their identity. */}}
{{- define "oracle-database-operator.clusterName" -}}
{{- $identity := printf "%s/%s" .Release.Namespace .Release.Name | sha256sum | trunc 12 -}}
{{- printf "%s-%s-%s" (include "oracle-database-operator.name" .) .Release.Name $identity -}}
{{- end }}

{{/* Route admission to the operator watching the resource's namespace. */}}
{{- define "oracle-database-operator.webhookNamespaceSelector" -}}
{{- if eq .Values.scope.mode "namespace" -}}
namespaceSelector:
  matchExpressions:
  - key: kubernetes.io/metadata.name
    operator: In
    values:
      {{- toYaml .Values.scope.watchNamespaces | nindent 6 }}
{{- end -}}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "oracle-database-operator.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "oracle-database-operator.labels" -}}
helm.sh/chart: {{ include "oracle-database-operator.chart" . }}
{{ include "oracle-database-operator.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "oracle-database-operator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "oracle-database-operator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
control-plane: controller-manager
{{- end }}

{{/*
Service account name
*/}}
{{- define "oracle-database-operator.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "oracle-database-operator.name" .) .Values.serviceAccount.name }}
{{- else }}
{{- required "serviceAccount.name is required when serviceAccount.create=false" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Controller manager image
*/}}
{{- define "oracle-database-operator.image" -}}
{{- $repository := printf "%s/%s" .Values.image.registry .Values.image.repository -}}
{{- if .Values.image.digest }}
{{- printf "%s@%s" $repository .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" $repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}
{{- end }}

{{/*
Webhook service name
*/}}
{{- define "oracle-database-operator.webhookServiceName" -}}
{{- printf "%s-webhook-service" (include "oracle-database-operator.name" .) }}
{{- end }}

{{/*
Metrics service name
*/}}
{{- define "oracle-database-operator.metricsServiceName" -}}
{{- printf "%s-controller-manager-metrics-service" (include "oracle-database-operator.name" .) }}
{{- end }}

{{/*
Certificate name
*/}}
{{- define "oracle-database-operator.certificateName" -}}
{{- printf "%s-serving-cert" (include "oracle-database-operator.name" .) }}
{{- end }}

{{/*
Metrics certificate name
*/}}
{{- define "oracle-database-operator.metricsCertificateName" -}}
{{- printf "%s-metrics-certs" (include "oracle-database-operator.name" .) }}
{{- end }}

{{/*
Issuer name
*/}}
{{- define "oracle-database-operator.issuerName" -}}
{{- printf "%s-selfsigned-issuer" (include "oracle-database-operator.name" .) }}
{{- end }}

{{/*
Cert-manager inject annotation value
*/}}
{{- define "oracle-database-operator.certManagerInjectAnnotation" -}}
{{- printf "%s/%s" (include "oracle-database-operator.namespace" .) (include "oracle-database-operator.certificateName" .) }}
{{- end }}
