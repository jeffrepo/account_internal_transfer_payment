from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AccountPayment(models.Model):
    _inherit = "account.payment"

    is_internal_transfer = fields.Boolean(
        string="Transferencia interna",
        copy=False,
        help="Marca el pago como una transferencia interna entre diarios bancarios o de caja de la misma compania.",
    )
    destination_journal_id = fields.Many2one(
        "account.journal",
        string="Diario de destino",
        check_company=True,
        copy=False,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id), ('id', '!=', journal_id)]",
    )
    paired_internal_transfer_payment_id = fields.Many2one(
        "account.payment",
        string="Pago espejo de transferencia interna",
        copy=False,
        readonly=True,
        check_company=True,
    )
    internal_transfer_pair_created = fields.Boolean(
        string="Par interno creado",
        default=False,
        copy=False,
        readonly=True,
    )

    @api.onchange("is_internal_transfer")
    def _onchange_is_internal_transfer(self):
        for rec in self:
            if rec.is_internal_transfer:
                rec.partner_id = False
                if "partner_bank_id" in rec._fields:
                    rec.partner_bank_id = False
                if "partner_type" in rec._fields:
                    rec.partner_type = False
                # El origen debe salir como pago saliente; el espejo entrante se crea automaticamente.
                if "payment_type" in rec._fields and rec.payment_type not in ("outbound", "inbound"):
                    rec.payment_type = "outbound"
            else:
                rec.destination_journal_id = False

    @api.onchange("journal_id", "company_id", "is_internal_transfer")
    def _onchange_destination_journal_id_domain(self):
        if not self.is_internal_transfer:
            return
        return {
            "domain": {
                "destination_journal_id": [
                    ("type", "in", ("bank", "cash")),
                    ("company_id", "=", self.company_id.id),
                    ("id", "!=", self.journal_id.id),
                ]
            }
        }

    @api.constrains("is_internal_transfer", "journal_id", "destination_journal_id", "company_id")
    def _check_internal_transfer_configuration(self):
        for rec in self.filtered("is_internal_transfer"):
            if not rec.journal_id:
                raise ValidationError(_("Debe seleccionar un diario origen para la transferencia interna."))
            if not rec.destination_journal_id:
                raise ValidationError(_("Debe seleccionar un diario de destino para la transferencia interna."))
            if rec.journal_id == rec.destination_journal_id:
                raise ValidationError(_("El diario origen y el diario destino deben ser distintos."))
            if rec.journal_id.company_id != rec.destination_journal_id.company_id:
                raise ValidationError(_("La transferencia interna debe realizarse entre diarios de la misma compania."))
            if rec.journal_id.type not in ("bank", "cash") or rec.destination_journal_id.type not in ("bank", "cash"):
                raise ValidationError(_("Solo se permiten diarios de tipo banco o caja para transferencias internas."))

    def _get_internal_transfer_account(self):
        self.ensure_one()
        company = self.company_id
        for attr in (
            "transfer_account_id",
            "internal_transfer_account_id",
            "account_journal_payment_transfer_account_id",
        ):
            if attr in company._fields:
                account = company[attr]
                if account:
                    return account
        return self.env["account.account"]

    def _prepare_internal_transfer_pair_vals(self):
        self.ensure_one()
        vals = {
            "payment_type": "inbound" if self.payment_type == "outbound" else "outbound",
            "amount": self.amount,
            "date": getattr(self, "date", False) or getattr(self, "payment_date", False),
            "payment_date": getattr(self, "payment_date", False) or getattr(self, "date", False),
            "memo": getattr(self, "memo", False) or getattr(self, "communication", False) or _("Transferencia interna: %s") % (self.display_name,),
            "communication": getattr(self, "communication", False) or getattr(self, "memo", False) or _("Transferencia interna: %s") % (self.display_name,),
            "journal_id": self.destination_journal_id.id,
            "destination_journal_id": self.journal_id.id,
            "currency_id": self.currency_id.id,
            "company_id": self.company_id.id,
            "is_internal_transfer": True,
            "paired_internal_transfer_payment_id": self.id,
            "internal_transfer_pair_created": True,
            "partner_id": False,
        }
        if "partner_type" in self._fields:
            vals["partner_type"] = False
        if "payment_method_line_id" in self._fields and self.payment_method_line_id:
            line = self.destination_journal_id.inbound_payment_method_line_ids[:1]
            if line:
                vals["payment_method_line_id"] = line.id
        elif "payment_method_id" in self._fields and self.payment_method_id:
            vals["payment_method_id"] = self.payment_method_id.id

        transfer_account = self._get_internal_transfer_account()
        if transfer_account and "destination_account_id" in self._fields:
            vals["destination_account_id"] = transfer_account.id
        return vals

    @api.depends_context('uid')
    def _compute_destination_account_id(self):
        """Fuerza la cuenta puente en transferencias internas para evitar CxC/CxP."""
        try:
            super()._compute_destination_account_id()
        except Exception:
            # En algunas variantes el campo puede no tener super compatible.
            pass
        for rec in self.filtered("is_internal_transfer"):
            transfer_account = rec._get_internal_transfer_account()
            if transfer_account:
                rec.destination_account_id = transfer_account

    def _synchronize_to_moves(self, changed_fields):
        # Dejar que el core sincronice, pero mantener la cuenta destino correcta.
        res = super()._synchronize_to_moves(changed_fields)
        for rec in self.filtered(lambda p: p.is_internal_transfer and p.state == 'draft'):
            transfer_account = rec._get_internal_transfer_account()
            if transfer_account and rec.move_id:
                lines = rec.move_id.line_ids.filtered(lambda l: not l.display_type and l.account_id != transfer_account)
                counterpart = rec.move_id.line_ids.filtered(lambda l: not l.display_type and l.account_id == rec.destination_account_id)
                if counterpart:
                    counterpart.account_id = transfer_account
        return res

    def _create_paired_internal_transfer_payment_fallback(self):
        for rec in self.filtered(lambda p: p.is_internal_transfer and not p.paired_internal_transfer_payment_id and p.state == "posted"):
            pair_vals = rec._prepare_internal_transfer_pair_vals()
            pair = self.with_context(skip_internal_transfer_pair=True).create(pair_vals)
            pair.action_post()
            rec.write({
                "paired_internal_transfer_payment_id": pair.id,
                "internal_transfer_pair_created": True,
            })
            pair.write({
                "paired_internal_transfer_payment_id": rec.id,
            })
            rec._reconcile_internal_transfer_with_pair(pair)

    def _reconcile_internal_transfer_with_pair(self, pair=None):
        for rec in self:
            pair = pair or rec.paired_internal_transfer_payment_id
            transfer_account = rec._get_internal_transfer_account()
            if not pair or not transfer_account or not rec.move_id or not pair.move_id:
                continue
            lines = (rec.move_id.line_ids | pair.move_id.line_ids).filtered(
                lambda l: not l.reconciled and not l.display_type and l.account_id == transfer_account
            )
            if len(lines) >= 2:
                try:
                    lines.reconcile()
                except Exception:
                    pass

    def action_post(self):
        for rec in self.filtered("is_internal_transfer"):
            if "partner_id" in rec._fields:
                rec.partner_id = False
            if "partner_type" in rec._fields:
                rec.partner_type = False
        res = super().action_post()
        if self.env.context.get("skip_internal_transfer_pair"):
            return res
        transfers = self.filtered(lambda p: p.is_internal_transfer and not p.paired_internal_transfer_payment_id)
        for rec in transfers:
            core_method = getattr(rec, "_create_paired_internal_transfer_payment", None)
            if callable(core_method):
                before_pair = rec.paired_internal_transfer_payment_id
                try:
                    core_method()
                except Exception:
                    before_pair = False
                rec.invalidate_recordset(["paired_internal_transfer_payment_id"])
                if rec.paired_internal_transfer_payment_id or before_pair:
                    rec.internal_transfer_pair_created = True
                    rec._reconcile_internal_transfer_with_pair()
                    continue
            rec._create_paired_internal_transfer_payment_fallback()
        return res

    def action_draft(self):
        for rec in self.filtered(lambda p: p.is_internal_transfer and p.paired_internal_transfer_payment_id and p.paired_internal_transfer_payment_id.state == "posted"):
            if not self.env.context.get("skip_internal_transfer_pair"):
                rec.paired_internal_transfer_payment_id.with_context(skip_internal_transfer_pair=True).action_draft()
        return super().action_draft()

    def action_cancel(self):
        for rec in self.filtered(lambda p: p.is_internal_transfer and p.paired_internal_transfer_payment_id and p.paired_internal_transfer_payment_id.state not in ("cancel",)):
            if not self.env.context.get("skip_internal_transfer_pair"):
                rec.paired_internal_transfer_payment_id.with_context(skip_internal_transfer_pair=True).action_cancel()
        return super().action_cancel()
