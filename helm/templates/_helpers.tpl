{{/*
Expand the name of the chart.
*/}}
{{- define "oracle-database-operator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "oracle-database-operator.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "oracle-database-operator.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Namespace for operator resources (hardcoded - required by CRD webhook configurations)
*/}}
{{- define "oracle-database-operator.namespace" -}}
oracle-database-operator-system
{{- end }}

{{/*
Check that cert-manager is installed.
Only runs during actual install (not helm template) by first checking cluster access.
Can be skipped by setting skipCertManagerCheck: true in values.
*/}}
{{- define "oracle-database-operator.checkCertManager" -}}
{{- if not .Values.skipCertManagerCheck -}}
{{- /* Check if we have cluster access by looking up kube-system namespace */ -}}
{{- if lookup "v1" "Namespace" "" "kube-system" -}}
{{- /* We have cluster access, now check for cert-manager CRD */ -}}
{{- if not (lookup "apiextensions.k8s.io/v1" "CustomResourceDefinition" "" "certificates.cert-manager.io") -}}
{{- fail "cert-manager is required but not installed. Please install cert-manager first: https://cert-manager.io/docs/installation/" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/*
Validate namespace-scoped configuration.
Fails if scope.mode is "namespace" but watchNamespaces is empty.
*/}}
{{- define "oracle-database-operator.validateScopeConfig" -}}
{{- if and (eq .Values.scope.mode "namespace") (empty .Values.scope.watchNamespaces) -}}
{{- fail "scope.watchNamespaces must not be empty when scope.mode is 'namespace'. Specify at least one namespace to watch." -}}
{{- end -}}
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
{{- if .Values.serviceAccount.name }}
{{- .Values.serviceAccount.name }}
{{- else }}
{{- "default" }}
{{- end }}
{{- end }}

{{/*
Controller manager image
*/}}
{{- define "oracle-database-operator.image" -}}
{{- printf "%s:%s" .Values.controllerManager.image.repository .Values.controllerManager.image.tag }}
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
