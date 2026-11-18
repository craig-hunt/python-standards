# {{ site_name }} dependency digest, {{ report_date }}

{% if run_result.status != "success" -%}
**This run did not finish cleanly.** {{ run_result.error_log or "No diagnostic was recorded, which is itself worth investigating." }}
{% endif %}
{% if new_findings -%}
## New since the last run

| Severity | Package | Finding |
|---|---|---|
{% for finding in new_findings -%}
| {{ finding.severity.value }} | {{ finding.subject }} | {{ finding.message }} |
{% endfor %}
{%- else -%}
No new findings.
{%- endif %}

{% if fix_list -%}
## Worth doing first

{% for finding in fix_list[:10] -%}
{{ loop.index }}. **{{ finding.message }}**<br>
   {{ finding.recommendation }}

{% endfor %}
{%- if fix_list|length > 10 %}
Plus {{ fix_list|length - 10 }} more, ranked below these.
{% endif %}
{%- endif %}
{% if resolved_findings %}
## Closed since the last run

{% for finding in resolved_findings -%}
- {{ finding.message }}
{% endfor %}
{%- endif %}
{% if notes %}
## Notes

{% for note in notes -%}
- {{ note }}
{% endfor %}
{%- endif %}

---

Checked {{ run_result.packages_checked }} package(s), {{ run_result.findings_total }} finding(s) open. Run took {{ run_result.duration_seconds }}s.
