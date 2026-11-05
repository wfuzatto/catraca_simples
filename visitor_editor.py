"""Editor de registros Visitor já existentes. Nunca cria pessoa/reserva."""
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
    requested = visitor_levels_for_group(
        group, available_groups, settings.get("group_access", {}),
        settings.get("preferred_group", ""))
    approved = VALE_VISITOR_LEVELS.get(group)
    if not approved or requested != [approved]:
        raise ValueError(
            "O grupo selecionado não possui o nível Visitor homologado. "
            "Confira Configurações; nenhuma alteração foi enviada.")
    return requested


def open_visitor_editor(app, record, *, remote_details=None, initial_photo=b"",
                        readonly=False, readonly_reason="", photo_error=""):
    # Nunca empilhar várias janelas modais por cliques repetidos.
    active = getattr(app, "_active_visitor_editor", None)
    if active is not None and active.winfo_exists():
        active.lift()
        active.focus_force()
        return
    if app.busy:
        messagebox.showwarning("Em andamento",
            "Aguarde a consulta atual antes de abrir o visitante.", parent=app)
        return
    # Uma operação pendente impede gravações, mas não pode impedir o operador
    # de VISUALIZAR um visitante e sua foto.
    if load_pending():
        readonly = True
        readonly_reason = ("Há uma operação de cadastro pendente neste Windows. "
                           "A visualização permanece disponível; resolva a pendência "
                           "na aba Cadastro para habilitar alterações.")
    dialog = ctk.CTkToplevel(app)
    app._active_visitor_editor = dialog
    dialog.title(("Visualizar cadastro" if readonly else "Editar cadastro") + " — " + record["visitor_id"])
    dialog.geometry("630x750")
    dialog.transient(app)
    dialog.grab_set()
    body = ctk.CTkScrollableFrame(dialog)
    body.pack(fill="both", expand=True, padx=14, pady=12)

    detail = remote_details if isinstance(remote_details, dict) else {}
    visitor = detail.get("VisitorInfo") or detail.get("visitorInfo") or detail
    if not isinstance(visitor, dict):
        visitor = {}
    raw_cards = visitor.get("cards") or []
    card_values = [str(x.get("cardNo") or "") for x in raw_cards if isinstance(x, dict)] \
        if isinstance(raw_cards, list) else []
    card_number = next((x for x in card_values if x), "") or record.get("card_number", "")
    visitor_photo_metadata = visitor.get("visitorPhoto") or visitor.get("VisitorPhoto") or {}
    if not isinstance(visitor_photo_metadata, dict):
        visitor_photo_metadata = {}
    has_remote_face = bool(
        record.get("photo_url") or visitor.get("faces") or
        visitor_photo_metadata.get("haveVisitorPhoto"))
    has_remote_card = bool(raw_cards or card_number)
    # A falta da foto pela API não deve tornar o formulário imutável.
    # Nesse cenário exige-se captura/substituição explícita ANTES de enviar.
    # Nunca faz update com foto vazia quando a imagem anterior pode existir.
    must_replace_photo = bool(
        record.get("origin") == "HikCentral" and
        (has_remote_face or photo_error) and not initial_photo)
    must_reenter_card = bool(
        record.get("origin") == "HikCentral" and
        has_remote_card and not card_number)
    if record.get("origin") == "HikCentral" and not readonly:
        if not has_remote_face and not has_remote_card and not photo_error:
            readonly = True
            readonly_reason = "A OpenAPI não confirmou nenhuma credencial original."

    ctk.CTkLabel(body, text="Foto atual no HikCentral", font=("Segoe UI", 14, "bold")).pack(pady=(0, 5))
    photo_panel = ctk.CTkLabel(
        body, text="Foto não disponível na API", width=200, height=200,
        fg_color=("#E9EEF5", "#243043"), corner_radius=10)
    photo_panel.pack(pady=(0, 7))
    dialog._photo_preview_widget = photo_panel  # Permite teste GUI e limpeza automática.
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
        photo_panel.configure(text="Foto indisponível")
    if (has_remote_face or photo_error) and not original_photo and not readonly:
        must_replace_photo = True
    if photo_error and not original_photo:
        ctk.CTkLabel(body, text="Foto não retornada: " + photo_error,
                     wraplength=530, justify="left").pack(pady=(0, 6))
    if not readonly and must_replace_photo:
        ctk.CTkLabel(body, text=(
            "Para preservar o reconhecimento facial, selecione ou capture "
            "uma NOVA foto antes de salvar. A original não foi recuperada. "
            "Nada será substituído antes da confirmação do cadastro."),
            text_color="#B45309", wraplength=530, justify="left").pack(pady=(0, 6))
    # A seleção do grupo determina o plano A APLICAR. Ela não é prova
    # de associação anterior (a OpenAPI pode omitir accessInfo). Evitar
    # o alerta laranja repetitivo; a confirmação do salvamento mostra
    # o grupo, nível e catracas efetivamente escolhidos.

    if readonly:
        ctk.CTkLabel(body,
                     text="Somente consulta: " + (readonly_reason or
                        "não foi possível validar todos os dados originais."),
                     text_color="#B14F26", wraplength=540,
                     justify="left").pack(pady=(0, 8))
    def formatted_visit_time(field):
        # Visitor Information contém pessoas sem um check-in no período.
        # Não fabricar datas nem falhar ao abrir a foto.
        try:
            value = record.get(field) or ""
            return datetime.fromisoformat(value).astimezone(TZ).strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            return ""

    initial = {
        "first": record.get("first", ""), "last": record.get("last", ""),
        "reservation": record.get("reservation", ""),
        "entry": formatted_visit_time("entry_at"),
        "exit": formatted_visit_time("exit_at"),
        "card": card_number if record.get("origin") == "HikCentral" else "",
    }
    fields = {}
    for field, label in [("first", "Nome"), ("last", "Sobrenome"),
                         ("reservation", "Reserva / apartamento"),
                         ("entry", "Entrada (DD/MM/AAAA)"),
                         ("exit", "Saída (DD/MM/AAAA)"),
                         ("card", "Pulseira/NFC (informar novamente)")]:
        ctk.CTkLabel(body, text=label, anchor="w").pack(fill="x", pady=(8, 2))
        var = tk.StringVar(value=initial[field])
        ctk.CTkEntry(body, textvariable=var,
                     state="disabled" if readonly else "normal").pack(fill="x")
        fields[field] = var
    ctk.CTkLabel(body, text="Grupo de visitante", anchor="w").pack(
        fill="x", pady=(10, 3))
    original_group = str(record.get("group", "")).strip()
    allowed_groups = list(dict.fromkeys(
        str(g).strip() for g in getattr(app, "connected_groups", [])
        if str(g).strip()))
    group_var = tk.StringVar(value=original_group)
    editor_groups = ttk.Combobox(
        body, textvariable=group_var, state=(
            "disabled" if readonly or not allowed_groups else "readonly"),
        values=allowed_groups, style="Vale.TCombobox", font=("Segoe UI", 12))
    editor_groups.pack(fill="x", ipady=5)
    dialog._editor_group_selector = editor_groups
    level_status = tk.StringVar()

    def show_selected_level(*_):
        group = group_var.get()
        try:
            level = selected_editor_access(
                group, allowed_groups, app.settings)[0]
            level_status.set(
                "Nível a aplicar: " + level +
                " • pontos de acesso consultados no HikCentral ao salvar")
        except ValueError:
            level_status.set(
                "Grupo sem mapeamento Visitor autorizado — salvar indisponível")
    group_var.trace_add("write", show_selected_level)
    show_selected_level()
    ctk.CTkLabel(body, textvariable=level_status,
                 wraplength=505, justify="left").pack(pady=(4, 10))
    if not readonly:
        ctk.CTkLabel(
            body, text=(
                "O grupo selecionado define as permissões A APLICAR ao salvar. "
                "O HikCentral pode omitir o nível anteriormente concedido; "
                "a seleção não comprova permissões antigas."),
            wraplength=505, justify="left", text_color="#64748B").pack(pady=(0, 5))
    ctk.CTkLabel(body,
        text="A imagem e a pulseira não ficam no histórico local. "
             "A edição exige as credenciais necessárias para não apagar "
             "um rosto ou cartão ainda usado. O Visitor ID será preservado.",
        wraplength=505, justify="left").pack(pady=8)
    chosen_photo = {"bytes": original_photo}
    status = tk.StringVar(value=(
        "Foto original carregada; selecione outra para substituir."
        if original_photo else "Nenhuma foto original disponível"))
    def update_photo_preview(data):
        try:
            with Image.open(BytesIO(data)) as source:
                source.thumbnail((220, 220), Image.Resampling.LANCZOS)
                frame = ImageTk.PhotoImage(source.copy())
            photo_panel.configure(image=frame, text="")
            photo_panel.image = frame
        except Exception as exc:
            status.set("Pré-visualização indisponível: " + str(exc))

    def select_photo():
        path = filedialog.askopenfilename(parent=dialog,
            filetypes=[("Fotos", "*.jpg *.jpeg *.png *.bmp")])
        if path:
            try:
                chosen_photo["bytes"] = prepare_face_jpeg(Path(path).read_bytes())
                status.set("Foto selecionada")
                update_photo_preview(chosen_photo["bytes"])
            except Exception as exc:
                messagebox.showerror("Foto", str(exc), parent=dialog)
    def use_camera_photo(stream):
        # Somente a foto deste registro editado; não modifica o novo cadastro.
        chosen_photo["bytes"] = prepare_face_jpeg(stream.read())
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
    ctk.CTkButton(photo_buttons, text="Selecionar foto",
                  command=select_photo,
                  state="disabled" if readonly else "normal").pack(side="left", padx=5)
    ctk.CTkButton(photo_buttons, text="Abrir câmera",
                  command=capture_photo,
                  state="disabled" if readonly else "normal").pack(side="left", padx=5)
    ctk.CTkLabel(body, textvariable=status).pack()
    def submit():
        if readonly:
            messagebox.showwarning("Edição protegida", readonly_reason, parent=dialog)
            return
        if app.busy or load_pending():
            messagebox.showwarning("Bloqueado", "Outra operação em andamento.", parent=dialog)
            return
        try:
            if must_replace_photo and not chosen_photo["bytes"]:
                raise ValueError(
                    "A foto original não foi recuperada. Capture ou selecione "
                    "uma nova foto antes de salvar; não vamos apagar a face antiga.")
            if must_reenter_card and not fields["card"].get().strip():
                raise ValueError("Reinforme a pulseira para preservar a credencial NFC.")
            if record.get("expect_face") and not chosen_photo["bytes"]:
                raise ValueError("Selecione a foto para preservar a credencial facial.")
            if record.get("expect_card") and not fields["card"].get().strip():
                raise ValueError("Informe a pulseira para preservar a credencial NFC.")
            new_group = group_var.get().strip()
            requested_level = selected_editor_access(
                new_group, allowed_groups, app.settings)
            visit_start, visit_end = visitor_period_from_dates(
                fields["entry"].get(), fields["exit"].get(), app.settings)
            obj = Visitor(fields["first"].get(), fields["last"].get(),
                new_group, chosen_photo["bytes"], fields["card"].get().strip(),
                visit_start, visit_end, fields["reservation"].get())
            try:
                original_start = datetime.fromisoformat(record.get("entry_at") or "").astimezone(TZ)
                original_end = datetime.fromisoformat(record.get("exit_at") or "").astimezone(TZ)
                validity_changed = original_start != visit_start or original_end != visit_end
            except (ValueError, TypeError):
                validity_changed = True
            payload = registration_payload(obj, 0)  # Nunca limitar utilizações na edição.
            if not record.get("registration_record_id"):
                raise ValueError("Este cadastro não contém ID de check-in para edição segura.")
            if not messagebox.askyesno(
                    "Confirmar atualização de visitante",
                    "Visitante ID: " + str(record["visitor_id"]) +
                    "\nGrupo selecionado: " + new_group +
                    "\nNível Visitor a aplicar: " + requested_level[0] +
                    "\n\n" +
                    ("ATENÇÃO: o grupo será alterado de '" + original_group +
                     "' para '" + new_group + "'. Isso altera as permissões.\n\n"
                     if new_group != original_group else
                     "Será reaplicado o nível configurado para esse grupo.\n\n") +
                    f"Validade a gravar: {visit_start:%d/%m/%Y %H:%M:%S} a "
                    f"{visit_end:%d/%m/%Y %H:%M:%S}.\n"
                    + ("ATENÇÃO: a validade anterior possui horários/período diferentes. "
                       "Ao salvar, eles serão substituídos pelos horários configurados "
                       "(padrão dia inteiro).\n\n" if validity_changed else "\n")
                    + "O registro de check-in será atualizado sem criar outra reserva. "
                    "Confirma a aplicação do grupo e nível selecionados?",
                    parent=dialog):
                return
        except Exception as exc:
            messagebox.showerror("Dados", str(exc), parent=dialog)
            return
        dialog.grab_release()
        dialog.withdraw()
        def work():
            client = app.client()
            old_id = client.find_existing_registration(
                record["visitor_id"], record["entry_at"], record["exit_at"])
            if not old_id or old_id != record["registration_record_id"]:
                raise ValueError("Check-in original não confirmado pela consulta.")
            # Revalidar grupo e acesso imediatamente antes da escrita.
            live_groups = client.visitor_groups()
            selected_group = selected_editor_access(
                new_group, live_groups, app.settings)
            if selected_group != requested_level:
                raise ValueError("O mapeamento do grupo foi alterado; edição bloqueada.")
            from remote_history import visitor_details
            details = visitor_details(client, record["visitor_id"])
            info = details.get("VisitorInfo") or details.get("visitorInfo") or details
            if not isinstance(info, dict):
                raise ValueError("O HikCentral não confirmou os dados atuais do visitante.")
            identity = str(info.get("visitorId") or info.get("ID") or "")
            actual_group = str(info.get("visitorGroupName") or
                               info.get("visitorGroup") or "")
            if (identity and identity != str(record["visitor_id"])) or (
                    actual_group and actual_group != original_group):
                raise ValueError(
                    "A identidade ou grupo foi alterado em outra estação. "
                    "Atualize a lista antes de editar.")
            selected, doors = client.resolve_levels(requested_level)
            verify_visitor_access_plan(selected, doors, requested_level)
            if new_group == original_group:
                if record.get("access_level_ids") and (
                        [str(x["id"]) for x in selected] !=
                        [str(x) for x in record["access_level_ids"]]):
                    raise ValueError("O nível original mudou. Edição bloqueada.")
                if record.get("door_ids") and set(doors) != set(record["door_ids"]):
                    raise ValueError("Pontos originais alterados; edição bloqueada.")
            # Ao trocar de grupo, a diferença de nível/catracas é intencional,
            # mas somente no plano homologado validado nesta mesma consulta.
            journal = app_dir() / "visitor_edit_inflight.json"
            if journal.exists():
                raise ValueError("Existe edição anterior com resultado incerto.")
            _write_json_atomic(journal, {"visitor_id": record["visitor_id"],
                "record_id": old_id, "status": "update_inflight"})
            # No retry if the remote update is accepted but its response is lost.
            new_id = client.update_registered_visitor(
                old_id, record["visitor_id"], payload, selected)
            save_registration({**record, "first": obj.first_name, "last": obj.last_name,
                "group": new_group, "access_names": requested_level,
                "access_level_ids": [str(selected[0]["id"])], "door_ids": list(doors),
                "reservation": obj.reservation, "entry_at": payload["visitStartTime"],
                "exit_at": payload["visitEndTime"], "registration_record_id": new_id,
                "state": "reaplicação pendente", "checkin_state": "a confirmar"})
            _write_json_atomic(journal, {"visitor_id": record["visitor_id"],
                "record_id": new_id, "status": "update_acknowledged"})
            client.apply(record["visitor_id"], doors)
            report = client.delivery_report(record["visitor_id"], doors,
                expect_face=bool(obj.photo_jpeg), expect_card=bool(obj.card_number))
            set_history_status(record["visitor_id"], state=report["state"],
                               checkin_state="a confirmar")
            report["access_receipt_saved"] = False
            if str(report.get("state") or "").casefold() == "confirmed":
                # Readback consultivo: o ID devolvido pelo UPDATE pode ser
                # appointRecordId e mudar após edição. A prova local só é
                # escrita quando o MESMO visitante/período foi reencontrado.
                try:
                    actual_id = client.find_existing_registration(
                        record["visitor_id"], payload["visitStartTime"],
                        payload["visitEndTime"])
                    if actual_id:
                        verified_record = {
                            **record,
                            "first": obj.first_name, "last": obj.last_name,
                            "reservation": obj.reservation, "group": new_group,
                            "entry_at": payload["visitStartTime"],
                            "exit_at": payload["visitEndTime"],
                            "registration_record_id": str(actual_id),
                            "access_names": [str(selected[0]["name"])],
                            "door_ids": list(doors),
                            "state": report["state"],
                            "checkin_state": "a confirmar",
                        }
                        receipt = make_access_receipt(
                            verified_record, level_id=selected[0]["id"],
                            door_ids=doors)
                        save_registration({**verified_record, "access_receipt": receipt})
                        report["access_receipt_saved"] = True
                except (ValueError, RuntimeError, OSError, KeyError):
                    # A atualização do servidor já foi aceita. Uma falha
                    # em reler ou salvar evidência local não justifica
                    # repetir o POST de escrita nem fingir confirmação.
                    pass
            journal.unlink(missing_ok=True)
            return report
        def done(result, error):
            app.refresh_history()
            if error:
                messagebox.showwarning("Edição não confirmada",
                    str(error) + "\nConfira no HikCentral antes de repetir a edição.",
                    parent=app)
            else:
                note = (
                    " A confirmação do nível foi registrada nesta estação "
                    "para evitar avisos repetidos sobre o mesmo check-in."
                    if result.get("access_receipt_saved") else
                    " O vínculo do nível continua não verificável pela OpenAPI; "
                    "a confirmação poderá ser solicitada novamente.")
                messagebox.showinfo("Atualização enviada",
                    "Mesmo visitante atualizado. Entrega: " + result["state"] +
                    ". Consulte o check-in para confirmação." + note, parent=app)
        app.task(work, done)
        dialog.destroy()
    save_button = ctk.CTkButton(body, text="Salvar e reenviar ao HikCentral",
                                command=submit,
                                state="disabled" if readonly else "normal")
    dialog._save_edit_button = save_button
    save_button.pack(pady=12)
    ctk.CTkButton(body, text="Cancelar", command=dialog.destroy).pack(pady=6)
