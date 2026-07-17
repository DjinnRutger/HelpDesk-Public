from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required

from ...forms import AISettingsForm
from ...models import Setting, Ticket, TicketEmbedding, TicketAISuggestion
from ... import db
from ...services.ai import check_health, get_ai_config, kick_index

from . import admin_bp, _bump_schedule_version  # noqa: F401


@admin_bp.route('/ai', methods=['GET', 'POST'])
@login_required
def ai_settings():
    form = AISettingsForm()
    if request.method == 'GET':
        cfg = get_ai_config()
        form.enabled.data = cfg['enabled']
        form.host.data = cfg['host']
        try:
            form.port.data = int(cfg['port'])
        except Exception:
            form.port.data = 11434
        form.chat_model.data = cfg['chat_model']
        form.embed_model.data = cfg['embed_model']
        form.auto_suggest.data = cfg['auto_suggest']
        form.index_interval.data = cfg['index_interval']
        form.similar_count.data = cfg['similar_count']

    if form.validate_on_submit() and 'submit' in request.form:
        Setting.set('AI_ENABLED', '1' if form.enabled.data else '0')
        Setting.set('AI_HOST', (form.host.data or '').strip())
        Setting.set('AI_PORT', str(form.port.data))
        Setting.set('AI_CHAT_MODEL', (form.chat_model.data or '').strip())
        Setting.set('AI_EMBED_MODEL', (form.embed_model.data or '').strip())
        Setting.set('AI_AUTO_SUGGEST_ENABLED', '1' if form.auto_suggest.data else '0')
        Setting.set('AI_INDEX_INTERVAL_MINUTES', str(form.index_interval.data))
        Setting.set('AI_SIMILAR_COUNT', str(form.similar_count.data))
        _bump_schedule_version()
        if form.enabled.data:
            kick_index()  # no-op outside single-process dev; scheduler owns it otherwise
        flash('Saved AI assistant settings', 'success')
        return redirect(url_for('admin.ai_settings'))

    status = {
        'ticket_count': Ticket.query.count(),
        'embedded_count': TicketEmbedding.query.count(),
        'suggestion_count': TicketAISuggestion.query.filter_by(status='ready').count(),
        'last_run': Setting.get('AI_INDEX_LAST_RUN', ''),
        'last_error': Setting.get('AI_LAST_ERROR', ''),
    }
    return render_template('admin/ai_settings.html', form=form, status=status)


@admin_bp.route('/ai/test', methods=['POST'])
@login_required
def ai_test():
    """AJAX connection test using the (possibly unsaved) host/port from the form."""
    host = (request.form.get('host') or '').strip() or None
    port = (request.form.get('port') or '').strip() or None
    ok, message, models = check_health(host, port)
    if ok and models:
        cfg = get_ai_config()
        chat_model = (request.form.get('chat_model') or cfg['chat_model']).strip()
        embed_model = (request.form.get('embed_model') or cfg['embed_model']).strip()
        missing = [m for m in (chat_model, embed_model)
                   if not any(name == m or name.startswith(m + ':') for name in models)]
        if missing:
            message += ' Warning: not installed on the server: ' + ', '.join(missing) + \
                       '. Run "ollama pull <model>" on the AI computer.'
    return jsonify({'success': ok, 'message': message, 'models': models})


@admin_bp.route('/ai/reindex', methods=['POST'])
@login_required
def ai_reindex():
    """Force a full re-embed on the next index run by clearing content hashes."""
    updated = TicketEmbedding.query.update({TicketEmbedding.content_hash: None,
                                            TicketEmbedding.updated_at: None})
    db.session.commit()
    _bump_schedule_version()
    kick_index()
    flash(f'Marked {updated} ticket embedding(s) for rebuild. The index job will re-embed all tickets on its next run.', 'success')
    return redirect(url_for('admin.ai_settings'))
