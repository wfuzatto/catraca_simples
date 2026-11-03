"""Editor de registros Visitor já existentes. Nunca cria pessoa/reserva."""
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageTk
from datetime import datetime
import customtkinter as ctk
from core import Visitor, TZ, parse_local, registration_payload, prepare_face_jpeg, load_pending, app_dir, _write_json_atomic, verify_visitor_access_plan
from visitor_history import save_registration, set_history_status



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


def open_visitor_editor(app, record, *, remote_details=None, initial_photo=b"",
                        readonly=False, readonly_reason="", photo_error=""):
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
    if record.get("origin") == "HikCentral" and not readonly:
        # Sem fotografia recuperada, a atualização poderia eliminar a
        # credencial facial anterior no endpoint registerment/update.
        if has_remote_face and not initial_photo:
            readonly = True
            readonly_reason = "A foto facial original não pôde ser recuperada."
        if has_remote_card and not card_number:
            readonly = True
            readonly_reason = "A pulseira original não foi retornada pela OpenAPI."
        if not has_remote_face and not has_remote_card:
            readonly = True
            readonly_reason = "A OpenAPI não confirmou as credenciais originais."

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
    if photo_error and not original_photo:
        ctk.CTkLabel(body, text="Foto não retornada: " + photo_error,
                     wraplength=530, justify="left").pack(pady=(0, 6))
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
            return datetime.fromisoformat(value).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
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
                         ("entry", "Entrada (DD/MM/AAAA HH:MM)"),
                         ("exit", "Saída (DD/MM/AAAA HH:MM)"),
                         ("card", "Pulseira/NFC (informar novamente)")]:
        ctk.CTkLabel(body, text=label, anchor="w").pack(fill="x", pady=(8, 2))
        var = tk.StringVar(value=initial[field])
        ctk.CTkEntry(body, textvariable=var,
                     state="disabled" if readonly else "normal").pack(fill="x")
        fields[field] = var
    ctk.CTkLabel(body,
        text="Grupo: " + record.get("group", "") + " | Nível: " +
             ", ".join(record.get("access_names", [])), wraplength=505).pack(pady=10)
    ctk.CTkLabel(body,
        text="A foto e a pulseira não são armazenadas no histórico. "
             "Reinforme as credenciais usadas antes de editar. "
             "O Visitor ID e os pontos autorizados serão preservados.",
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
            if record.get("expect_face") and not chosen_photo["bytes"]:
                raise ValueError("Selecione novamente a foto para preservar a credencial facial.")
            if record.get("expect_card") and not fields["card"].get().strip():
                raise ValueError("Informe a pulseira para preservar a credencial NFC.")
            obj = Visitor(fields["first"].get(), fields["last"].get(),
                record["group"], chosen_photo["bytes"], fields["card"].get().strip(),
                parse_local(fields["entry"].get()), parse_local(fields["exit"].get()),
                fields["reservation"].get())
            payload = registration_payload(obj, 0)  # Nunca limitar utilizações na edição.
            if not record.get("registration_record_id"):
                raise ValueError("Este cadastro não contém ID de check-in para edição segura.")
            if not messagebox.askyesno("Confirmar edição",
                "Atualizar o Visitor ID original e reaplicar a credencial às mesmas catracas?",
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
            selected, doors = client.resolve_levels(record["access_names"])
            if record.get("access_level_ids") and (
                    [str(x["id"]) for x in selected] !=
                    [str(x) for x in record["access_level_ids"]]):
                raise ValueError("O nível original mudou. Edição bloqueada.")
            verify_visitor_access_plan(selected, doors, record["access_names"])
            if set(doors) != set(record["door_ids"]):
                raise ValueError("Pontos alterados; edição bloqueada.")
            journal = app_dir() / "visitor_edit_inflight.json"
            if journal.exists():
                raise ValueError("Existe edição anterior com resultado incerto.")
            _write_json_atomic(journal, {"visitor_id": record["visitor_id"],
                "record_id": old_id, "status": "update_inflight"})
            # No retry if the remote update is accepted but its response is lost.
            new_id = client.update_registered_visitor(
                old_id, record["visitor_id"], payload, selected)
            save_registration({**record, "first": obj.first_name, "last": obj.last_name,
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
            journal.unlink(missing_ok=True)
            return report
        def done(result, error):
            app.refresh_history()
            if error:
                messagebox.showwarning("Edição não confirmada",
                    str(error) + "\nConfira no HikCentral antes de repetir a edição.",
                    parent=app)
            else:
                messagebox.showinfo("Atualização enviada",
                    "Mesmo visitante atualizado. Entrega: " + result["state"] +
                    ". Consulte o check-in para confirmação.", parent=app)
        app.task(work, done)
        dialog.destroy()
    save_button = ctk.CTkButton(body, text="Salvar e reenviar ao HikCentral",
                                command=submit,
                                state="disabled" if readonly else "normal")
    dialog._save_edit_button = save_button
    save_button.pack(pady=12)
    ctk.CTkButton(body, text="Cancelar", command=dialog.destroy).pack(pady=6)
