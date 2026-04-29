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
        domain="[(\'type\', \'in\', (\'bank\', \'cash\')), (\'company_id\', \'=\', company_id)]",
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
                if rec.payment_type not in ("outbound", "inbound"):
                    rec.payment_type = "outbound"
                rec.partner_id = False
                rec.partner_bank_id = False
            else:
                rec.destination_journal_id = False

    @api.onchange("journal_id", "company_id", "is_internal_transfer")
    def _onchange_destination_journal_id_domain(self):
        for rec in self:
            if not rec.is_internal_transfer:
                continue
            journals = self.env["account.journal"].search([
                ("type", "in", ("bank", "cash")),
                ("company_id", "=", rec.company_id.id),
                ("id", "!=", rec.journal_id.id),
            ])
            return {"domain": {"destination_journal_id": [("id", "in", journals.ids)]}}

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

    @api.depends("is_internal_transfer", "destination_journal_id")
    def _compute_partner_id(self):
        internal_transfers = self.filtered("is_internal_transfer")
        non_internal = self - internal_transfers
        super(AccountPayment, non_internal)._compute_partner_id()
        for rec in internal_transfers:
            rec.partner_id = False

    def _get_internal_transfer_account(self):
        self.ensure_one()
        company = self.company_id
        for attr in (
            "transfer_account_id",
            "internal_transfer_account_id",
            "account_journal_payment_transfer_account_id",
        ):
            if hasattr(company, attr):
                account = getattr(company, attr)
                if account:
                    return account
        return self.env["account.account"]

    def _prepare_internal_transfer_pair_vals(self):
        self.ensure_one()
        vals = {
            "payment_type": "inbound" if self.payment_type == "outbound" else "outbound",
            "amount": self.amount,
            "date": self.date,
            "memo": self.memo or _("Transferencia interna: %s") % (self.name or "/"),
            "journal_id": self.destination_journal_id.id,
            "destination_journal_id": self.journal_id.id,
            "currency_id": self.currency_id.id,
            "company_id": self.company_id.id,
            "partner_type": self.partner_type or "customer",
            "is_internal_transfer": True,
            "paired_internal_transfer_payment_id": self.id,
            "internal_transfer_pair_created": True,
        }
        transfer_account = self._get_internal_transfer_account()
        if transfer_account:
            vals["destination_account_id"] = transfer_account.id
        return vals

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

    def action_post(self):
        res = super().action_post()
        if self.env.context.get("skip_internal_transfer_pair"):
            return res
        transfers = self.filtered(lambda p: p.is_internal_transfer and not p.paired_internal_transfer_payment_id)
        for rec in transfers:
            core_method = getattr(rec, "_create_paired_internal_transfer_payment", None)
            if callable(core_method):
                before_pairs = rec.paired_internal_transfer_payment_id
                core_method()
                rec.invalidate_recordset(["paired_internal_transfer_payment_id"])
                if rec.paired_internal_transfer_payment_id or before_pairs:
                    rec.internal_transfer_pair_created = True
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
