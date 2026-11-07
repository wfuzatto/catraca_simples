"""Editor de perfis e visitas já existentes no HikCentral."""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageTk
from datetime import datetime
import customtkinter as ctk
from core import (Visitor, TZ, visitor_period_from_dates, registration_payload,
                  prepare_face_jpeg, load_pending, app_dir, _write_json_atomic,
                  verify_visitor_access_plan, visitor_levels_for_group,
                  VALE_VISITOR_LEVELS)
from visitor_history import save_registration, set_history_status
from access_receipt import make_access_receipt
from remote_history import reservation_period_notice



def open_editor_camera(dialog, settings, on_accept):
    """Abre a mesma webcam guiada do cadastro, mas direciona a foto ao editor.

    O grab modal do editor é transferido para a câmera e restaurado ao fechar.
    Não altera app.photo_data nem envia imagem ao HikCentral nesta etapa.
    """
    from face_capture_ui import GuidedFaceCamera

    camera = None
    dialog.grab_release()
    try:
        camera = GuidedFaceCamera(
            dialog, settings.get("camera_index", 0), on_accept, settings)

        def restore_editor(event):
            if event.widget is camera.window and dialog.winfo_exists():
                dialog.grab_set()
                dialog.lift()
                dialog.focus_force()

        camera.window.bind("<Destroy>", restore_editor, add="+")
        camera.window.wait_visibility()
        camera.window.grab_set()
        camera.window.focus_force()
        return camera
    except Exception:
        if camera is not None and not camera.closed:
            camera.close()
        if dialog.winfo_exists():
            dialog.grab_set()
        raise


def selected_editor_access(group, available_groups, settings):
    """Select the operator-approved mapping; never infer actual existing rights.

    Uses exactly the same group restrictions and group_access settings as the
    main registration screen; remote type=2 privilege group and doors are
    independently checked just before applying any changes.
    """
    group = str(group or "").strip()
    # A preferência de grupo vale para novos cadastros neste caixa, não
    # para editar um visitante existente de outro grupo já validado.
    requested = visitor_levels_for_group(
        group, available_groups, settings.get("group_access", {}), "")
    approved = VALE_VISITOR_LEVELS.get(group)
    if not approved or requested != [approved]:
        raise ValueError(
            "O grupo selecionado não possui o nível Visitor homologado. "
            "Confira Configurações; nenhuma alteração foi enviada.")
    return requested


def open_visitor_editor(app, record, *, remote_details=None, initial_photo=b"",
                        readonly=False, readonly_reason="", photo_error=""):
    """Keep the form editable; require the operator to complete missing data."""
    from editor_validation import (CREDENTIAL_MODES, EDIT_OPERATIONS,
                                   editor_requirements, infer_edit_operation,
                                   reservation_edit_dates, validate_editor_fields)
    from hikcentral import normalize

    active = getattr(app, "_active_visitor_editor", None)
    if active is not None and active.winfo_exists():
        active.lift()
        active.focus_force()
        return
    dialog = ctk.CTkToplevel(app)
    app._active_visitor_editor = dialog
    dialog.title("Editar cadastro — " + str(record.get("visitor_id") or "visitante"))
    dialog.geometry("660x790")
    dialog.transient(app)
    dialog.grab_set()
    body = ctk.CTkScrollableFrame(dialog)
    body.pack(fill="both", expand=True, padx=14, pady=12)

    detail = remote_details if isinstance(remote_details, dict) else {}
    visitor = detail.get("VisitorInfo") or detail.get("visitorInfo") or detail
    visitor = visitor if isinstance(visitor, dict) else {}
    raw_cards = visitor.get("cards") or []
    card_values = [str(x.get("cardNo") or "") for x in raw_cards if isinstance(x, dict)] \
        if isinstance(raw_cards, list) else []
    card_number = next((x for x in card_values if x), "") or str(record.get("card_number") or "")
    original_group = str(record.get("group") or "").strip()
    remote_group = str(visitor.get("visitorGroupName") or visitor.get("visitorGroup") or "").strip()
    missing_fields = record.get("_editor_missing_fields") or {}
    group_recovery = (not original_group or original_group not in VALE_VISITOR_LEVELS or
                      bool(remote_group and normalize(remote_group) != normalize(original_group)) or
                      "group" in missing_fields)
    available = list(dict.fromkeys(str(g).strip() for g in getattr(app, "connected_groups", [])
                                   if str(g).strip() in VALE_VISITOR_LEVELS))
    for group in (original_group, remote_group):
        if group in VALE_VISITOR_LEVELS and group not in available:
            available.append(group)
    if not available:
        available = list(VALE_VISITOR_LEVELS)
    allowed_groups = available if group_recovery else [original_group]

    notes = []
    if readonly_reason:
        notes.append("Complete os dados indicados para salvar. " + str(readonly_reason))
    try:
        if load_pending():
            notes.append("Há uma operação pendente neste computador; os campos continuam editáveis e a gravação será conferida para este visitante.")
    except Exception:
        notes.append("A consulta de pendências não terminou; a conferência será refeita ao salvar.")
    if notes:
        ctk.CTkLabel(body, text="\n".join(notes), wraplength=590,
                     justify="left", text_color="#64748B").pack(fill="x", pady=(0, 8))

    widgets, error_labels, remote_errors, fields = {}, {}, {}, {}
    selected_visit, visit_choices = {"raw": None}, {}
    visit_var = tk.StringVar()
    fields["visit"] = visit_var
    visit_border = ctk.CTkFrame(body, border_width=2, border_color="#9CA3AF")
    ctk.CTkLabel(visit_border, text="Selecione a visita que deseja corrigir", anchor="w").pack(fill="x", padx=5)
    visit_selector = ttk.Combobox(visit_border, textvariable=visit_var, state="readonly", font=("Segoe UI", 11))
    visit_selector.pack(fill="x", padx=5, pady=3)
    error_labels["visit"] = ctk.CTkLabel(visit_border, text="", text_color="#B91C1C", wraplength=580, justify="left")
    error_labels["visit"].pack(fill="x", padx=5)
    widgets["visit"] = visit_border
    dialog._editor_visit_selector = visit_selector
    operation_hint = infer_edit_operation(record)
    operation_var = tk.StringVar(value=next((label for label, code in EDIT_OPERATIONS.items()
                                           if code == operation_hint), ""))
    fields["operation"] = operation_var
    operation_border = ctk.CTkFrame(body, border_width=2, border_color="#9CA3AF")
    ctk.CTkLabel(operation_border, text="Operação para este visitante", anchor="w").pack(fill="x", padx=5)
    operation_selector = ttk.Combobox(operation_border, textvariable=operation_var,
                                     state="readonly", values=list(EDIT_OPERATIONS))
    operation_selector.pack(fill="x", padx=5, pady=3)
    widgets["operation"] = operation_border
    error_labels["operation"] = ctk.CTkLabel(operation_border, text="", text_color="#B91C1C",
                                            wraplength=580, justify="left")
    error_labels["operation"].pack(fill="x", padx=5)
    dialog._editor_operation_selector = operation_selector
    if not operation_hint:
        operation_border.pack(fill="x", pady=(3, 7))
    ctk.CTkLabel(body, text="Foto do visitante", font=("Segoe UI", 14, "bold")).pack(pady=(0, 4))
    photo_border = ctk.CTkFrame(body, border_width=2, border_color="#9CA3AF")
    photo_border.pack(pady=(0, 3))
    photo_panel = ctk.CTkLabel(photo_border, text="Selecione uma foto ou use a câmera",
                             width=220, height=200, fg_color=("#E9EEF5", "#243043"))
    photo_panel.pack(padx=3, pady=3)
    widgets["photo"] = photo_border
    error_labels["photo"] = ctk.CTkLabel(body, text="", text_color="#B91C1C", wraplength=590, justify="left")
    error_labels["photo"].pack(fill="x")
    dialog._photo_preview_widget = photo_panel
    original_photo = b""
    try:
        if initial_photo:
            original_photo = prepare_face_jpeg(initial_photo)
            with Image.open(BytesIO(original_photo)) as image:
                image.thumbnail((220, 220), Image.Resampling.LANCZOS)
                preview = ImageTk.PhotoImage(image.copy())
            photo_panel.configure(image=preview, text="")
            photo_panel.image = preview
    except Exception:
        original_photo = b""
    requirements = editor_requirements(record, visitor, photo_error=photo_error,
                                       photo_loaded=bool(original_photo))
    chosen_photo = {"bytes": original_photo}
    dialog._editor_requirements = requirements
    if photo_error and not original_photo:
        ctk.CTkLabel(body, text="A foto não foi recuperada. Selecione uma nova imagem ou use a câmera.",
                     wraplength=590, justify="left", text_color="#64748B").pack(fill="x", pady=(0, 5))

    booking_period = record.get("_reservation_period") or {}
    initial_dates, baseline_dates = reservation_edit_dates(record)

    initial = {
        "first": visitor.get("visitorGivenName", record.get("first", "")),
        "last": visitor.get("visitorFamilyName", record.get("last", "")),
        "reservation": visitor.get("remark", record.get("reservation", "")),
        "entry": initial_dates["entry"], "exit": initial_dates["exit"],
        "card": card_number,
    }
    for field, label in (("first", "Nome"), ("last", "Sobrenome"),
                         ("reservation", "Apartamento (Remark)"),
                         ("entry", "Entrada da reserva (DD/MM/AAAA)"), ("exit", "Saída da reserva (DD/MM/AAAA)"),
                         ("card", "Pulseira/NFC")):
        ctk.CTkLabel(body, text=label, anchor="w").pack(fill="x", pady=(7, 2))
        fields[field] = tk.StringVar(value=initial[field] or "")
        entry = ctk.CTkEntry(body, textvariable=fields[field], state="normal", border_width=2)
        entry.pack(fill="x")
        widgets[field] = entry
        error_labels[field] = ctk.CTkLabel(body, text="", text_color="#B91C1C", wraplength=590, justify="left")
        error_labels[field].pack(fill="x")

    appointment_var = tk.StringVar(value=str(record.get("appointment_id") or
                                            record.get("_original_appointment_id") or
                                            booking_period.get("appointment_id") or ""))
    fields["appointment"] = appointment_var
    appointment_border = ctk.CTkFrame(body, border_width=2, border_color="#9CA3AF")
    ctk.CTkLabel(appointment_border, text="Reserva vinculada no HikCentral (ID)",
                 anchor="w").pack(fill="x", padx=5)
    appointment_selector = ttk.Combobox(appointment_border, textvariable=appointment_var,
                                       state="normal", values=[appointment_var.get()] if appointment_var.get() else [])
    appointment_selector.pack(fill="x", padx=5, pady=3)
    widgets["appointment"] = appointment_border
    error_labels["appointment"] = ctk.CTkLabel(appointment_border, text="", text_color="#B91C1C",
                                              wraplength=580, justify="left")
    error_labels["appointment"].pack(fill="x", padx=5)
    dialog._editor_appointment_selector = appointment_selector
    booking_anchor = ctk.CTkFrame(body, fg_color="transparent", height=1)
    booking_anchor.pack(fill="x")

    ctk.CTkLabel(body, text="Grupo de visitantes" if group_recovery else "Grupo original",
                 anchor="w").pack(fill="x", pady=(7, 2))
    group_var = tk.StringVar(value="" if group_recovery else original_group)
    fields["group"] = group_var
    group_border = ctk.CTkFrame(body, border_width=2, border_color="#9CA3AF", corner_radius=4)
    group_border.pack(fill="x")
    editor_groups = ttk.Combobox(group_border, textvariable=group_var, state="readonly",
                                values=allowed_groups, style="Vale.TCombobox", font=("Segoe UI", 12))
    editor_groups.pack(fill="x", padx=3, pady=3, ipady=5)
    widgets["group"] = group_border
    error_labels["group"] = ctk.CTkLabel(body, text="", text_color="#B91C1C", wraplength=590, justify="left")
    error_labels["group"].pack(fill="x")
    dialog._editor_group_selector = editor_groups
    if group_recovery:
        ctk.CTkLabel(body, text="O grupo não foi confirmado. Selecione o valor correto antes de salvar.",
                     wraplength=590, justify="left").pack(fill="x")
    else:
        ctk.CTkLabel(body, text="O grupo original e seu nível de acesso serão preservados.",
                     wraplength=590, justify="left", text_color="#64748B").pack(fill="x")

    ctk.CTkLabel(body, text="Credenciais deste visitante", anchor="w").pack(fill="x", pady=(7, 2))
    mode_var = tk.StringVar(value=requirements["initial_credential_mode"])
    fields["credential_mode"] = mode_var
    mode_border = ctk.CTkFrame(body, border_width=2, border_color="#9CA3AF", corner_radius=4)
    mode_border.pack(fill="x")
    credential_modes = ttk.Combobox(mode_border, textvariable=mode_var, state="readonly",
                                   values=list(CREDENTIAL_MODES), font=("Segoe UI", 12))
    credential_modes.pack(fill="x", padx=3, pady=3, ipady=5)
    widgets["credential_mode"] = mode_border
    error_labels["credential_mode"] = ctk.CTkLabel(body, text="", text_color="#B91C1C", wraplength=590, justify="left")
    error_labels["credential_mode"].pack(fill="x")
    dialog._editor_credential_selector = credential_modes

    level_status = tk.StringVar()
    ctk.CTkLabel(body, textvariable=level_status, wraplength=590, justify="left").pack(fill="x", pady=(4, 5))
    period_notice = "\n".join(reservation_period_notice(
        booking_period, record.get("entry_at"), record.get("exit_at")).splitlines()[:2])
    if booking_period.get("state") == "different" and initial_dates != baseline_dates:
        period_notice += "\nAs datas do check-in foram carregadas. Salvar vai substituir a reserva para usar esse período."
    period_label = ctk.CTkLabel(body, text=period_notice, wraplength=590, justify="left", text_color="#64748B")
    if period_notice:
        period_label.pack(fill="x", pady=(4, 5))
    dialog._reservation_period_label = period_label
    summary = tk.StringVar()
    summary_label = ctk.CTkLabel(body, textvariable=summary, wraplength=590, justify="left")
    summary_label.pack(fill="x", pady=(4, 5))
    status = tk.StringVar(value="Foto original carregada" if original_photo else "Nenhuma foto selecionada")

    def values():
        return {key: var.get() for key, var in fields.items()}

    def refresh_validation(changed=None):
        if changed is not None:
            remote_errors.pop(changed, None)
            related = {"entry": ("exit",), "exit": ("entry",),
                       "first": ("last",), "last": ("first",),
                       "card": ("credential_mode",), "photo": ("credential_mode",)}
            for field in related.get(changed, ()):
                remote_errors.pop(field, None)
        errors = validate_editor_fields(values(), app.settings, allowed_groups,
                                        photo_bytes=chosen_photo["bytes"], **requirements)
        if operation_var.get() == "Renovar visita" or requirements["require_appointment"]:
            appointment_border.pack(fill="x", pady=(7, 3), before=booking_anchor)
        else:
            appointment_border.pack_forget()
        errors.update(remote_errors)
        dialog._editor_field_errors = dict(errors)
        for field, widget in widgets.items():
            widget.configure(border_color="#DC2626" if field in errors else "#9CA3AF")
            error_labels[field].configure(text=errors.get(field, ""))
        selected_group = group_var.get().strip()
        try:
            level = selected_editor_access(selected_group, allowed_groups, app.settings)[0]
            level_status.set("Nível Visitor: " + level + " • será conferido no HikCentral ao salvar")
        except ValueError:
            level_status.set("Selecione um grupo com nível Visitor válido.")
        summary.set("Complete os campos em vermelho antes de salvar." if errors else "Dados preenchidos. Você pode salvar o cadastro.")
        summary_label.configure(text_color="#B91C1C" if errors else "#15803D")
        return errors

    dialog._editor_field_vars = fields
    dialog._editor_field_widgets = widgets
    dialog._editor_error_labels = error_labels
    dialog._editor_refresh_validation = refresh_validation
    for field, var in fields.items():
        if field != "visit":
            var.trace_add("write", lambda *_args, key=field: refresh_validation(key))

    def choose_visit(*_):
        selected_visit["raw"] = visit_choices.get(visit_var.get())
        if selected_visit["raw"] is not None:
            refresh_validation("visit")
    visit_var.trace_add("write", choose_visit)

    def update_photo_preview(data):
        with Image.open(BytesIO(data)) as source:
            source.thumbnail((220, 220), Image.Resampling.LANCZOS)
            frame = ImageTk.PhotoImage(source.copy())
        photo_panel.configure(image=frame, text="")
        photo_panel.image = frame
        refresh_validation("photo")

    def select_photo():
        path = filedialog.askopenfilename(parent=dialog, filetypes=[("Fotos", "*.jpg *.jpeg *.png *.bmp")])
        if path:
            try:
                chosen_photo["bytes"] = prepare_face_jpeg(Path(path).read_bytes())
                chosen_photo["changed"] = True
                status.set("Foto selecionada")
                update_photo_preview(chosen_photo["bytes"])
            except Exception as exc:
                messagebox.showerror("Foto", str(exc), parent=dialog)

    def use_camera_photo(stream):
        chosen_photo["bytes"] = prepare_face_jpeg(stream.read())
        chosen_photo["changed"] = True
        status.set("Foto capturada pela câmera")
        update_photo_preview(chosen_photo["bytes"])
        return True

    def capture_photo():
        try:
            open_editor_camera(dialog, app.settings, use_camera_photo)
        except Exception as exc:
            messagebox.showerror("Webcam", str(exc), parent=dialog)

    photo_buttons = ctk.CTkFrame(body, fg_color="transparent")
    photo_buttons.pack(pady=5)
    photo_button = ctk.CTkButton(photo_buttons, text="Selecionar foto", command=select_photo)
    photo_button.pack(side="left", padx=5)
    camera_button = ctk.CTkButton(photo_buttons, text="Abrir câmera", command=capture_photo)
    camera_button.pack(side="left", padx=5)
    dialog._editor_photo_button = photo_button
    dialog._editor_camera_button = camera_button
    ctk.CTkLabel(body, textvariable=status).pack()

    def select_confirmed_active_visit():
        """A new confirmed check-in is now the visit to correct on the next save."""
        record.pop("_selected_checkin_record", None)
        record.pop("_selected_checkin_id", None)
        selected_visit["raw"] = None
        visit_choices.clear()
        visit_var.set("")
        visit_border.pack_forget()
        operation_var.set("Atualizar visita existente")
        requirements["require_operation"] = False
        requirements["require_appointment"] = False
        operation_border.pack_forget()
        appointment_border.pack_forget()

    def flag_delivery_refusal(result, visitor_obj):
        """Highlight a rejected credential, or require a fresh choice if unknown."""
        rows = result.get("doors") or []
        face_states = [str(row.get("faceState") or "").casefold()
                       for row in rows if isinstance(row, dict)]
        card_states = [str(row.get("credentialState") or "").casefold()
                       for row in rows if isinstance(row, dict)]
        failed_photo = "failed" in face_states
        failed_card = "failed" in card_states
        if str(result.get("state") or "").casefold() == "failed" and not (failed_photo or failed_card):
            failed_photo = bool(requirements.get("require_photo") or visitor_obj.photo_jpeg) and (
                not face_states or any(state != "confirmed" for state in face_states))
            failed_card = bool(requirements.get("require_card") or visitor_obj.card_number) and (
                not card_states or any(state != "confirmed" for state in card_states))
            if not (failed_photo or failed_card):
                mode_var.set("")
                remote_errors["credential_mode"] = (
                    "O HikCentral confirmou os dados, mas não aplicou o acesso. "
                    "Selecione novamente a modalidade de credencial para repetir a entrega.")
        if failed_photo:
            requirements["require_photo"] = True
            remote_errors["photo"] = "Foto recusada ou não confirmada. Selecione uma nova foto de rosto."
        if failed_card:
            requirements["require_card"] = True
            remote_errors["card"] = "Pulseira recusada ou não confirmada. Confira e informe o número novamente."
        return failed_photo, failed_card

    def submit():
        if getattr(dialog, "_edit_submitting", False):
            return
        if app.busy:
            status.set("Aguarde a operação atual terminar; os dados digitados serão mantidos.")
            return
        errors = refresh_validation()
        if errors:
            first_error = next(iter(errors))
            target = widgets.get(first_error)
            if target is not None:
                target.focus_set()
            messagebox.showerror("Complete os campos indicados",
                                 "\n".join(dict.fromkeys(errors.values())), parent=dialog)
            return
        try:
            new_group = group_var.get().strip()
            requested_level = selected_editor_access(new_group, allowed_groups, app.settings)
            visit_start, visit_end = visitor_period_from_dates(fields["entry"].get(), fields["exit"].get(), app.settings)
            obj = Visitor(fields["first"].get(), fields["last"].get(), new_group,
                          chosen_photo["bytes"], fields["card"].get().strip(),
                          visit_start, visit_end, fields["reservation"].get())
            payload = registration_payload(obj, 0, editing=True)
            date_changed = any(
                datetime.strptime(fields[key].get().strip(), "%d/%m/%Y").date() !=
                datetime.strptime(baseline_dates[key], "%d/%m/%Y").date()
                for key in ("entry", "exit")
            )
            if date_changed and not appointment_var.get().strip():
                requirements["require_appointment"] = True
                remote_errors["appointment"] = "Confirme o ID da reserva original no HikCentral antes de alterar as datas."
                refresh_validation()
                appointment_selector.focus_set()
                messagebox.showerror("Reserva original necessária",
                    "O campo Reserva vinculada ficou vermelho. Selecione ou informe o ID da reserva original para continuar.",
                    parent=dialog)
                return
            operation = EDIT_OPERATIONS.get(operation_var.get()) or record.get("_edit_operation")
            if date_changed:
                summary_text = (
                    f"Visitante ID: {record.get('visitor_id') or ''}\n"
                    f"Reserva original: {appointment_var.get().strip()}\n"
                    f"Novas datas: {visit_start:%d/%m/%Y %H:%M:%S} a {visit_end:%d/%m/%Y %H:%M:%S}\n\n"
                    "A troca seguirá esta ordem no HikCentral:\n"
                    "1. Fazer checkout da visita atual.\n"
                    "2. Excluir a reserva original.\n"
                    "3. Criar outra reserva para o mesmo Visitor ID.\n"
                    "4. Confirmar o check-in, se o HikCentral não o fizer automaticamente.\n\n"
                    "O Edge abrirá para você entrar no Web Client. O checkout só começa depois que a sessão e as permissões forem confirmadas."
                )
                if not messagebox.askyesno("Confirmar troca de datas", summary_text, parent=dialog):
                    return
            else:
                summary_text = (
                    "Os dados preenchidos serão conferidos em Visitor Information.\n"
                    "As datas atuais da reserva não foram alteradas. A visita mantém o período já registrado.\n\n"
                    + (period_notice + "\n\n" if period_notice else "")
                    + "Confirma o envio dos dados?"
                )
                if not messagebox.askyesno("Confirmar edição do cadastro", summary_text, parent=dialog):
                    return
        except Exception as exc:
            messagebox.showerror("Dados do cadastro", str(exc), parent=dialog)
            return

        selected_profile_photo = chosen_photo["bytes"] if chosen_photo.get("changed") else b""
        appointment_id = appointment_var.get().strip()
        operation_value = EDIT_OPERATIONS.get(operation_var.get(), "")
        operator_selected_group = bool(group_recovery)
        chosen_visit = selected_visit["raw"]
        dialog._edit_submitting = True
        dialog.grab_release()
        dialog.withdraw()
        loading = ctk.CTkToplevel(app)
        loading.withdraw()
        loading.title("Salvando cadastro")
        loading.geometry("445x160")
        loading.resizable(False, False)
        loading.transient(app)
        loading.protocol("WM_DELETE_WINDOW", lambda: None)
        frame = ctk.CTkFrame(loading, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=22, pady=18)
        ctk.CTkLabel(frame, text="Salvando alterações no HikCentral...", font=("Segoe UI", 15, "bold")).pack(pady=(4, 7))
        ctk.CTkLabel(frame, text="Conferindo cadastro, reserva e permissões. Aguarde.", font=("Segoe UI", 12)).pack(pady=(0, 10))
        progress = ctk.CTkProgressBar(frame, mode="indeterminate", width=380)
        progress.pack(fill="x")
        progress.start()
        loading.deiconify()
        loading.lift()
        loading.grab_set()
        dialog._edit_loading_overlay = loading
        dialog._edit_loading_bar = progress

        def close_edit_loading():
            progress.stop()
            if loading.winfo_exists():
                try:
                    loading.grab_release()
                except Exception:
                    pass
                loading.destroy()
            dialog._edit_submitting = False

        def work():
            client = app.client()
            live_groups = client.visitor_groups()
            current_level = selected_editor_access(new_group, live_groups, app.settings)
            if current_level != requested_level:
                raise ValueError("Selecione novamente o grupo: o nível Visitor mudou no servidor.")
            selected, doors = client.resolve_levels(requested_level)
            verify_visitor_access_plan(selected, doors, requested_level)
            edit_record = dict(record)
            edit_record["_operator_selected_group"] = operator_selected_group
            edit_record["appointment_id"] = appointment_id
            edit_record["_operator_edit_operation"] = operation_value
            if chosen_visit is not None:
                edit_record["_selected_checkin_record"] = chosen_visit
                edit_record["_selected_checkin_id"] = str(chosen_visit.get("recordId") or "")
            from visitor_profile import update_profile
            if date_changed:
                from contextlib import ExitStack
                from native_booking_bridge import open_booking_bridge
                from ordered_reservation_change import change_reservation_dates
                from remote_history import original_appointment_identifier
                with ExitStack() as stack:
                    def prepare_creation(original, target_payload):
                        bridge = stack.enter_context(open_booking_bridge(
                            app.settings["host"], app.settings["port"], timeout_seconds=180,
                            cert_pin_sha256=app.settings.get("cert_pin_sha256", "")))
                        return bridge.prepare(
                            str(record["visitor_id"]), target_payload["visitStartTime"],
                            target_payload["visitEndTime"], selected,
                            expected_booking_id=original_appointment_identifier(original) if original else None,
                            first_name=obj.first_name, last_name=obj.last_name, remark=obj.reservation,
                            purpose_type=target_payload.get("visitPurposeType", 4),
                            purpose_detail=target_payload.get("visitPurpose", ""))
                    def prepare_and_save_profile(original, target_payload):
                        create = prepare_creation(original, target_payload)
                        update_profile(client, str(record["visitor_id"]), obj.first_name, obj.last_name,
                                       card=obj.card_number, new_photo=selected_profile_photo)
                        return create
                    return change_reservation_dates(
                        client, edit_record, payload, selected, doors, obj, new_group, requested_level,
                        prepare_creation=prepare_and_save_profile)
            update_profile(client, str(record["visitor_id"]), obj.first_name, obj.last_name,
                           card=obj.card_number, new_photo=selected_profile_photo)
            from copy import deepcopy
            from visitor_edit_transaction import update_existing_visit
            visit_payload = deepcopy(payload)
            if record.get("entry_at") and record.get("exit_at"):
                visit_payload["visitStartTime"] = record["entry_at"]
                visit_payload["visitEndTime"] = record["exit_at"]
            return update_existing_visit(client, edit_record, visit_payload,
                                         selected, doors, obj, new_group, requested_level)

        def done(result, error):
            nonlocal group_recovery, period_notice
            close_edit_loading()
            if error:
                choices = getattr(error, "choices", {}) or {}
                if isinstance(choices, dict) and "appointment" in choices:
                    appointments = []
                    for item in choices.get("appointment") or []:
                        value = (item.get("appointRecordId") or item.get("appointID") or
                                 item.get("appointment_id") or "") if isinstance(item, dict) else item
                        if str(value or "").strip():
                            appointments.append(str(value).strip())
                    appointment_selector.configure(values=list(dict.fromkeys(appointments)))
                    requirements["require_appointment"] = True
                    appointment_border.pack(fill="x", pady=(7, 3), before=booking_anchor)
                if isinstance(choices, dict) and isinstance(choices.get("operation"), list):
                    operation_selector.configure(values=choices["operation"])
                    operation_var.set("")
                    requirements["require_operation"] = True
                    requirements["require_appointment"] = False
                    operation_border.pack(fill="x", pady=(3, 7), before=photo_border)
                if isinstance(choices, dict) and isinstance(choices.get("group"), list):
                    allowed_groups[:] = list(dict.fromkeys(str(x) for x in choices["group"] if str(x)))
                    editor_groups.configure(values=allowed_groups)
                    group_recovery = True
                    group_var.set("")
                if isinstance(choices, dict) and isinstance(choices.get("visit"), list):
                    visit_choices.clear()
                    statuses = {"0": "Check-in", "1": "Check-out", "2": "Saída automática",
                                "3": "Saída no terminal", "4": "Saída em atraso"}
                    for row in choices["visit"]:
                        if not isinstance(row, dict):
                            continue
                        base = row.get("visitorBaseInfo") or {}
                        start, end = str(base.get("visitStartTime") or ""), str(base.get("visitEndTime") or "")
                        try:
                            start = datetime.fromisoformat(start).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
                            end = datetime.fromisoformat(end).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
                        except ValueError:
                            pass
                        label = (start + " → " + end + " · " + statuses.get(str(row.get("visitorStatus")), "Visita")
                                 + " · " + str(row.get("recordId") or "")[-6:])
                        visit_choices[label] = row
                    visit_selector.configure(values=list(visit_choices))
                    visit_var.set("")
                    selected_visit["raw"] = None
                    visit_border.pack(fill="x", pady=(3, 7), before=photo_border)
                supplied = getattr(error, "fields", None) or getattr(error, "field_errors", None)
                if isinstance(supplied, dict):
                    if "appointment" in supplied:
                        requirements["require_appointment"] = True
                        appointment_border.pack(fill="x", pady=(7, 3), before=booking_anchor)
                    if "operation" in supplied:
                        requirements["require_operation"] = True
                        operation_border.pack(fill="x", pady=(3, 7), before=photo_border)
                    remote_errors.update({key: str(value) for key, value in supplied.items() if key in widgets})
                else:
                    message = str(error).casefold()
                    for field, words in (("photo", ("foto", "facial")),
                                         ("card", ("pulseira", "cartão", "cardnumber")),
                                         ("group", ("grupo", "nível", "pontos")),
                                         ("exit", ("validade", "datas", "período"))):
                        if any(word in message for word in words):
                            remote_errors[field] = str(error)
                if dialog.winfo_exists():
                    dialog.deiconify()
                    dialog.grab_set()
                    dialog.lift()
                    refresh_validation()
                messagebox.showwarning("Não foi possível concluir todas as etapas",
                    str(error) + "\n\nConfira os campos destacados e salve novamente. O aplicativo consulta a situação da reserva antes de repetir uma etapa.",
                    parent=dialog if dialog.winfo_exists() else app)
                return

            app.refresh_history()
            if result.get("rebooking_completed"):
                new_appointment = str(result.get("confirmed_appointment_id") or "")
                new_checkin = str(result.get("confirmed_checkin_id") or "")
                booking_period = result.get("reservation_period") or {}
                record.update(appointment_id=new_appointment, _original_appointment_id=new_appointment,
                              registration_record_id=new_checkin, entry_at=payload["visitStartTime"],
                              exit_at=payload["visitEndTime"], first=obj.first_name.strip(),
                              last=obj.last_name.strip(), reservation=obj.reservation.strip(), group=new_group,
                              checkin_state="check-in efetuado", _edit_operation="update",
                              _reservation_period=booking_period)
                select_confirmed_active_visit()
                appointment_var.set(new_appointment)
                baseline_dates["entry"] = fields["entry"].get().strip()
                baseline_dates["exit"] = fields["exit"].get().strip()
                if selected_profile_photo:
                    chosen_photo["changed"] = False
                booking_note = reservation_period_notice(
                    booking_period, payload["visitStartTime"], payload["visitEndTime"])
                if booking_note:
                    period_notice = booking_note
                    period_label.configure(text=booking_note, text_color="#64748B")
                    period_label.pack(fill="x", pady=(4, 5), before=summary_label)
                remote_errors.clear()
                flag_delivery_refusal(result, obj)
                if result.get("state") == "confirmed":
                    messagebox.showinfo("Reserva e check-in confirmados",
                        f"A reserva {booking_period.get('code') or new_appointment} foi criada para o mesmo visitante.\n"
                        + ("O HikCentral fez o check-in automaticamente." if result.get("automatic_checkin")
                           else "O aplicativo confirmou o check-in após criar a reserva.")
                        + "\nAs datas e o apartamento foram conferidos no HikCentral.", parent=app)
                    if dialog.winfo_exists():
                        dialog.destroy()
                    return
                detail = str(result.get("message") or "A entrega nas catracas ainda precisa ser confirmada.")
                status.set("Reserva e check-in confirmados; confira a entrega indicada em vermelho.")
                if dialog.winfo_exists():
                    dialog.deiconify()
                    dialog.grab_set()
                    dialog.lift()
                    refresh_validation()
                messagebox.showwarning("Reserva criada; entrega pendente",
                    f"A reserva {booking_period.get('code') or new_appointment} e o check-in foram confirmados.\n"
                    + detail + "\n\nCorrija a credencial destacada e salve novamente. A reserva não será recriada.",
                    parent=dialog if dialog.winfo_exists() else app)
                return

            booking_period = result.get("reservation_period") or {}
            booking_note = reservation_period_notice(
                booking_period, result.get("visit_start_at") or record.get("entry_at", ""),
                result.get("visit_end_at") or record.get("exit_at", ""))
            if booking_period:
                record["_reservation_period"] = booking_period
                if booking_period.get("appointment_id"):
                    record["appointment_id"] = booking_period["appointment_id"]
                    appointment_var.set(str(booking_period["appointment_id"]))
            record.update(first=obj.first_name.strip(), last=obj.last_name.strip(),
                          reservation=obj.reservation.strip(), group=new_group)
            if selected_profile_photo:
                chosen_photo["changed"] = False
            if booking_note:
                period_notice = booking_note
                period_label.configure(text=booking_note, text_color="#64748B")
                period_label.pack(fill="x", pady=(4, 5), before=summary_label)
            if str(result.get("state") or "").casefold() == "failed":
                record.update(registration_record_id=str(result["confirmed_checkin_id"]),
                              _original_checkin_id=str(result["confirmed_checkin_id"]),
                              _update_record_id=str(result["confirmed_checkin_id"]),
                              entry_at=result.get("visit_start_at") or record.get("entry_at"),
                              exit_at=result.get("visit_end_at") or record.get("exit_at"),
                              checkin_state="check-in efetuado", _edit_operation="update")
                select_confirmed_active_visit()
                remote_errors.clear()
                flag_delivery_refusal(result, obj)
                status.set("Dados gravados no perfil e na visita. Corrija a credencial recusada em vermelho.")
                if dialog.winfo_exists():
                    dialog.deiconify()
                    dialog.grab_set()
                    dialog.lift()
                    refresh_validation()
                messagebox.showwarning("Dados gravados; credencial recusada",
                    "Nome, sobrenome, apartamento e período da visita foram gravados.\n"
                    "O HikCentral recusou uma credencial em uma ou mais catracas.\n"
                    "Corrija o campo indicado e salve novamente. A visita existente será corrigida sem criar outra reserva."
                    + ("\n\n" + booking_note if booking_note else ""), parent=dialog)
                return
            messagebox.showinfo("Edição conferida",
                "Nome, sobrenome, apartamento, credenciais e visita foram conferidos em Visitor Information.\n"
                "As datas da reserva não foram alteradas."
                + ("\n\n" + booking_note if booking_note else ""), parent=app)
            if dialog.winfo_exists():
                dialog.destroy()

        app.task(work, done)

    save_button = ctk.CTkButton(body, text="Salvar alterações", command=submit, state="normal")
    dialog._save_edit_button = save_button
    save_button.pack(pady=12)
    ctk.CTkButton(body, text="Cancelar", command=dialog.destroy).pack(pady=6)
    refresh_validation()
