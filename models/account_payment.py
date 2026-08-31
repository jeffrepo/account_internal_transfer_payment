from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from markupsafe import Markup



class AccountPayment(models.Model):
    _inherit = "account.payment"

    is_internal_transfer = fields.Boolean(
        string="Transferencia interna",
        copy=False,
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
        string="Pago espejo",
        copy=False,
        readonly=True,
        check_company=True,
    )
    internal_transfer_pair_created = fields.Boolean(
        string="Par creado",
        default=False,
        copy=False,
        readonly=True,
    )

    @api.onchange("is_internal_transfer")
    def _onchange_is_internal_transfer(self):
        for rec in self:
            if rec.is_internal_transfer:
                internal_partner = rec._get_internal_transfer_partner()
                rec.partner_id = internal_partner
                rec.partner_type = "supplier"
                rec.payment_type = "outbound"
                if "partner_bank_id" in rec._fields:
                    rec.partner_bank_id = False
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

    def _is_internal_transfer_flow(self):
        self.ensure_one()
        return bool(self.is_internal_transfer or self.paired_internal_transfer_payment_id)

    @api.depends(
        "move_id.name",
        "state",
        "is_internal_transfer",
        "paired_internal_transfer_payment_id",
    )
    def _compute_name(self):
        normal_payments = self.filtered(
            lambda payment: not payment._is_internal_transfer_flow()
        )
        if normal_payments:
            super(AccountPayment, normal_payments)._compute_name()

        for payment in self - normal_payments:
            if payment.state not in ("in_process", "paid"):
                continue

            move_name = payment.move_id.name
            payment.name = move_name if move_name and move_name != "/" else False
    
    @api.depends("company_id", "partner_id", "payment_type", "is_internal_transfer", "paired_internal_transfer_payment_id")
    def _compute_journal_id(self):
        normal_payments = self.filtered(lambda p: not (p.is_internal_transfer or p.paired_internal_transfer_payment_id))
        if normal_payments:
            super(AccountPayment, normal_payments)._compute_journal_id()

        internal_payments = self - normal_payments
        for pay in internal_payments:
            company = pay.company_id or self.env.company

            # Si ya tiene diario seleccionado y pertenece a la compañía, NO lo sobreescribas
            if pay.journal_id and pay.journal_id.company_id == company:
                continue

            # Si viene de un registro ya existente, conserva su diario original
            if pay._origin and pay._origin.journal_id and pay._origin.journal_id.company_id == company:
                pay.journal_id = pay._origin.journal_id
                continue

            # Fallback únicamente si no hay diario
            pay.journal_id = self.env["account.journal"].search([
                *self.env["account.journal"]._check_company_domain(company),
                ("type", "in", ("bank", "cash")),
            ], limit=1)

    @api.depends("available_payment_method_line_ids", "payment_type", "journal_id", "is_internal_transfer", "paired_internal_transfer_payment_id")
    def _compute_payment_method_line_id(self):
        normal_payments = self.filtered(lambda p: not (p.is_internal_transfer or p.paired_internal_transfer_payment_id))
        if normal_payments:
            super(AccountPayment, normal_payments)._compute_payment_method_line_id()

        internal_payments = self - normal_payments
        for pay in internal_payments:
            available = pay.available_payment_method_line_ids

            # Si el actual sigue siendo válido, conservarlo
            if pay.payment_method_line_id and pay.payment_method_line_id in available:
                continue

            if pay.payment_type == "outbound":
                candidates = pay.journal_id.outbound_payment_method_line_ids
            else:
                candidates = pay.journal_id.inbound_payment_method_line_ids

            candidates = candidates.filtered(lambda l: l in available)

            if candidates:
                pay.payment_method_line_id = candidates[0]
            elif available:
                pay.payment_method_line_id = available[0]
            else:
                pay.payment_method_line_id = False

    def _get_internal_transfer_partner(self):
        self.ensure_one()
        if not self.company_id.partner_id:
            raise ValidationError(_("La compañía no tiene un contacto configurado."))
        return self.company_id.partner_id

    def _get_internal_transfer_inbound_method_line(self):
        self.ensure_one()

        inbound_method_lines = self.destination_journal_id.inbound_payment_method_line_ids
        if not inbound_method_lines:
            raise UserError(
                _("El diario destino %s no tiene método de pago de entrada configurado.")
                % self.destination_journal_id.display_name
            )

        configured_method_lines = inbound_method_lines.filtered("payment_account_id")
        if not configured_method_lines:
            raise UserError(
                _(
                    "Configura una cuenta transitoria en un método de pago de entrada "
                    "del diario destino %s. Sin esa cuenta Odoo no puede generar el "
                    "asiento ni la secuencia del pago espejo."
                )
                % self.destination_journal_id.display_name
            )

        manual_method_line = configured_method_lines.filtered(
            lambda line: line.code == "manual"
        )[:1]
        return manual_method_line or configured_method_lines[:1]

    def _check_internal_transfer_move_configuration(self):
        for payment in self:
            if not payment.outstanding_account_id:
                raise UserError(
                    _(
                        "Configura una cuenta transitoria en el método de pago %s "
                        "del diario origen %s."
                    )
                    % (
                        payment.payment_method_line_id.display_name,
                        payment.journal_id.display_name,
                    )
                )
            payment._get_internal_transfer_inbound_method_line()
        
    @api.constrains("is_internal_transfer", "journal_id", "destination_journal_id", "company_id")
    def _check_internal_transfer_configuration(self):
        for rec in self.filtered("is_internal_transfer"):
            if not rec.journal_id:
                raise ValidationError(_("Debe seleccionar un diario origen."))
            if not rec.destination_journal_id:
                raise ValidationError(_("Debe seleccionar un diario destino."))
            if rec.journal_id == rec.destination_journal_id:
                raise ValidationError(_("El diario origen y destino deben ser diferentes."))
            if rec.journal_id.company_id != rec.destination_journal_id.company_id:
                raise ValidationError(_("Los diarios deben pertenecer a la misma compañía."))
            if rec.journal_id.type not in ("bank", "cash") or rec.destination_journal_id.type not in ("bank", "cash"):
                raise ValidationError(_("Solo se permiten diarios de banco o caja."))
            if not rec.company_id.transfer_account_id:
                raise ValidationError(_("Configura la cuenta de transferencia interna en la compañía."))

    @api.depends("journal_id", "partner_id", "partner_type", "is_internal_transfer", "company_id")
    def _compute_destination_account_id(self):
        super()._compute_destination_account_id()
        for pay in self:
            if pay.is_internal_transfer:
                pay.destination_account_id = pay.company_id.transfer_account_id

    def _prepare_move_liquidity_lines(self, default_values):
        lines = super()._prepare_move_liquidity_lines(default_values)
        if self.is_internal_transfer:
            for line in lines:
                line["partner_id"] = False
        return lines

    def _prepare_move_counterpart_lines(self, default_values):
        lines = super()._prepare_move_counterpart_lines(default_values)
        if self.is_internal_transfer:
            for line in lines:
                line["partner_id"] = False
                line["account_id"] = self.company_id.transfer_account_id.id
        return lines

    @api.model_create_multi
    def create(self, vals_list):
        payments = super().create(vals_list)

        for pay, vals in zip(payments, vals_list):
            if vals.get("is_internal_transfer"):
                if vals.get("journal_id"):
                    pay.journal_id = vals["journal_id"]
                if vals.get("payment_method_line_id"):
                    pay.payment_method_line_id = vals["payment_method_line_id"]

        return payments
    
    def write(self, vals):
        vals = dict(vals)
        for rec in self:
            if vals.get("is_internal_transfer") or rec.is_internal_transfer:
                company = rec.company_id or self.env.company
                if not vals.get("company_id") and company.partner_id:
                    vals.setdefault("partner_id", company.partner_id.id)
                vals.setdefault("partner_type", "supplier")
                vals.setdefault("payment_type", "outbound")
        return super().write(vals)

    def _prepare_internal_transfer_pair_vals(self):
        self.ensure_one()

        inbound_method_line = self._get_internal_transfer_inbound_method_line()
    
        internal_partner = self.company_id.partner_id
        if not internal_partner:
            raise ValidationError(_("La compañía no tiene un contacto configurado."))
    
        destination_currency = self.destination_journal_id.currency_id or self.company_id.currency_id
    
        if destination_currency == self.currency_id:
            destination_amount = self.amount
        else:
            destination_amount = self.currency_id._convert(
                self.amount,
                destination_currency,
                self.company_id,
                self.date,
            )
    
        return {
            "date": self.date,
            "journal_id": self.destination_journal_id.id,
            "company_id": self.company_id.id,
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": internal_partner.id,
            "amount": destination_amount,
            "currency_id": destination_currency.id,
            "memo": self.memo or _("Transferencia interna desde %s") % self.journal_id.display_name,
            "payment_method_line_id": inbound_method_line.id,
            "is_internal_transfer": False,
            "destination_account_id": self.company_id.transfer_account_id.id,
            "paired_internal_transfer_payment_id": self.id,
        }

    def _post_internal_transfer_links_to_chatter(self, other_payment):
        self.ensure_one()
    
        payment_link = Markup(
            '<a href="/web#id=%s&model=account.payment&view_type=form">%s</a>'
        ) % (other_payment.id, other_payment.display_name)
    
        body = Markup("Se ha creado un segundo pago: %s") % payment_link
    
        if other_payment.move_id:
            move_link = Markup(
                '<a href="/web#id=%s&model=account.move&view_type=form">%s</a>'
            ) % (other_payment.move_id.id, other_payment.move_id.display_name)
    
            body += Markup("<br/>Se ha generado el asiento: %s") % move_link
    
        self.message_post(
            body=body,
            subtype_xmlid="mail.mt_note",
        )
        
    def _create_internal_transfer_pair(self):
        for pay in self.filtered(lambda p: p.is_internal_transfer and not p.paired_internal_transfer_payment_id):
            pair_vals = pay._prepare_internal_transfer_pair_vals()
            pair = self.env["account.payment"].create(pair_vals)
    
            pay.paired_internal_transfer_payment_id = pair.id
            pay.internal_transfer_pair_created = True
    
            pair.action_post()

            if not pair.move_id:
                raise UserError(
                    _(
                        "No se pudo generar el asiento del pago espejo en el diario %s. "
                        "Revisa su método de pago de entrada y su cuenta transitoria."
                    )
                    % pair.journal_id.display_name
                )
            pair._compute_name()
    
            counterpart_origin = pay.move_id.line_ids.filtered(
                lambda l: l.account_id == pay.company_id.transfer_account_id and not l.reconciled
            )
            counterpart_pair = pair.move_id.line_ids.filtered(
                lambda l: l.account_id == pay.company_id.transfer_account_id and not l.reconciled
            )
    
            lines_to_reconcile = counterpart_origin | counterpart_pair
            if len(lines_to_reconcile) >= 2:
                lines_to_reconcile.reconcile()
    
            pay._post_internal_transfer_links_to_chatter(pair)
            pair._post_internal_transfer_links_to_chatter(pay)

    def action_post(self):
        internal_payments = self.filtered(
            lambda payment: payment.is_internal_transfer
            and not payment.internal_transfer_pair_created
        )
        internal_payments._check_internal_transfer_move_configuration()

        res = super().action_post()
        if internal_payments.filtered(lambda payment: not payment.move_id):
            raise UserError(
                _(
                    "No se pudo generar el asiento del pago origen. Revisa el método "
                    "de pago y la cuenta transitoria del diario seleccionado."
                )
            )
        internal_payments._compute_name()
        internal_payments._create_internal_transfer_pair()
        return res
