# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
import time
from odoo import api, models
from odoo.tools import float_is_zero, float_compare
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT

D_LEDGER = {'general': {'name': 'General Ledger',
                        'group_by': 'account_id',
                        'model': 'account.account',
                        'short': 'code',
                        },
            'partner': {'name': 'Partner Ledger',
                        'group_by': 'partner_id',
                        'model': 'res.partner',
                        'short': 'name',
                        },
            'journal': {'name': 'Journal Ledger',
                        'group_by': 'journal_id',
                        'model': 'account.journal',
                        'short': 'code',
                        },
            'open': {'name': 'Open Ledger',
                     'group_by': 'account_id',
                     'model': 'account.account',
                     'short': 'code',
                     },
            }


class AccountExtraReport(models.AbstractModel):
    _name = 'report.account_standard_report.report_account_standard_report'

    def _generate_sql(self, type_ledger, data, accounts, date_to, date_from):
        date_clause = ''
        if date_to:
            date_clause += ' AND account_move_line.date <= ' + "'" + str(date_to) + "'" + ' '
        if date_from and type_ledger == 'journal':
            date_clause += ' AND account_move_line.date >= ' + "'" + str(date_from) + "'" + ' '

        # clear used_context date if not it is use during the sql query
        data['form']['used_context']['date_to'] = False
        data['form']['used_context']['date_from'] = False

        query_get_data = self.env['account.move.line'].with_context(data['form'].get('used_context', {}))._query_get()
        reconcile_clause = data['reconcile_clause']
        params = [tuple(data['computed']['move_state']), tuple(accounts.ids)] + query_get_data[2]

        partner_clause = ''
        if data['form'].get('partner_ids'):
            partner_ids = data['form'].get('partner_ids')
            if len(partner_ids) == 1:
                partner_ids = "(%s)" % (partner_ids[0])
            else:
                partner_ids = tuple(partner_ids)
            partner_clause = ' AND account_move_line.partner_id IN ' + str(partner_ids) + ' '
        elif type_ledger == 'partner':
            partner_clause = ' AND account_move_line.partner_id IS NOT NULL '

        query = """
            SELECT
                account_move_line.id,
                account_move_line.date,
                account_move_line.date_maturity,
                j.code,
                acc.code AS a_code,
                acc.name AS a_name,
                acc_type.type AS a_type,
                acc_type.include_initial_balance AS include_initial_balance,
                account_move_line.ref,
                m.name AS move_name,
                account_move_line.name,
                account_move_line.debit,
                account_move_line.credit,
                account_move_line.amount_currency,
                account_move_line.currency_id,
                c.symbol AS currency_code,
                afr.name AS matching_number,
                afr.id AS matching_number_id,
                account_move_line.partner_id,
                account_move_line.account_id,
                account_move_line.journal_id,
                prt.name AS partner_name
            FROM """ + query_get_data[0] + """
                LEFT JOIN account_journal j ON (account_move_line.journal_id = j.id)
                LEFT JOIN account_account acc ON (account_move_line.account_id = acc.id)
                LEFT JOIN account_account_type acc_type ON (acc.user_type_id = acc_type.id)
                LEFT JOIN res_currency c ON (account_move_line.currency_id = c.id)
                LEFT JOIN account_move m ON (account_move_line.move_id = m.id)
                LEFT JOIN account_full_reconcile afr ON (account_move_line.full_reconcile_id = afr.id)
                LEFT JOIN res_partner prt ON (account_move_line.partner_id = prt.id)
            WHERE
                m.state IN %s
                AND account_move_line.account_id IN %s AND """ + query_get_data[1] + reconcile_clause + partner_clause + date_clause + """
                ORDER BY account_move_line.date, move_name, a_code, account_move_line.ref"""
        self.env.cr.execute(query, tuple(params))
        return self.env.cr.dictfetchall()

    def _generate_account_dict(self, accounts):
        line_account = {}
        for account in accounts:
            line_account[account.id] = {
                'debit': 0.0,
                'credit': 0.0,
                'balance': 0.0,
                'code': account.code,
                'name': account.name,
                'active': False,
            }
        return line_account

    def _generate_init_balance_lines(self, type_ledger, init_lines_to_compact, init_balance_history):
        group_by_field = D_LEDGER[type_ledger]['group_by']
        rounding = self.env.user.company_id.currency_id.rounding or 0.01
        init_lines = {}
        for r in init_lines_to_compact:
            key = (r['account_id'], r[group_by_field])
            reduce_balance = r['reduce_balance'] and not init_balance_history
            if key in init_lines.keys():
                if reduce_balance:
                    init_lines[key]['re_debit'] += r['debit']
                    init_lines[key]['re_credit'] += r['credit']
                else:
                    init_lines[key]['debit'] += r['debit']
                    init_lines[key]['credit'] += r['credit']
            else:
                init_lines[key] = {'debit': r['debit'] if not reduce_balance else 0,
                                   'credit': r['credit'] if not reduce_balance else 0,
                                   're_debit': r['debit'] if reduce_balance else 0,
                                   're_credit': r['credit'] if reduce_balance else 0,
                                   'account_id': r['account_id'],
                                   group_by_field: r[group_by_field],
                                   'a_code': r['a_code'],
                                   'a_type': r['a_type'], }
        init = []
        for key, value in init_lines.items():
            init_debit = value['debit']
            init_credit = value['credit']
            balance = init_debit - init_credit
            re_balance = value['re_debit'] - value['re_credit']
            if float_is_zero(balance, rounding):
                balance = 0.0
            if re_balance > 0:
                init_debit += abs(re_balance)
            elif re_balance < 0:
                init_credit += abs(re_balance)

            if not float_is_zero(init_debit, rounding) or not float_is_zero(init_credit, rounding):
                init.append({'date': 'Initial balance',
                             'date_maturity': '',
                             'debit': init_debit,
                             'credit': init_credit,
                             'code': 'INIT',
                             'a_code': value['a_code'],
                             'move_name': '',
                             'account_id': value['account_id'],
                             group_by_field: value[group_by_field],
                             'displayed_name': '',
                             'partner_name': '',
                             'progress': balance,
                             'amount_currency': 0.0,
                             'matching_number': '',
                             'type_line': 'init'})
        return init

    def _generate_total(self, sum_debit, sum_credit, balance):
        rounding = self.env.user.company_id.currency_id.rounding or 0.01
        return {'date': 'Total',
                'date_maturity': '',
                'debit': sum_debit,
                'credit': sum_credit,
                's_debit': False if float_is_zero(sum_debit, rounding) else True,
                's_credit': False if float_is_zero(sum_credit, rounding) else True,
                'code': '',
                'move_name': '',
                'a_code': '',
                'account_id': '',
                'displayed_name': '',
                'partner_name': '',
                'progress': balance,
                'amount_currency': 0.0,
                'matching_number': '',
                'type_line': 'total', }

    def _generate_data(self, type_ledger, data, accounts, date_format):
        rounding = self.env.user.company_id.currency_id.rounding or 0.01
        with_init_balance = data['form']['with_init_balance']
        init_balance_history = data['form']['init_balance_history']
        summary = data['form']['summary']
        date_from = data['form']['used_context']['date_from']
        date_to = data['form']['used_context']['date_to']
        detail_unreconcillied_in_init = data['form']['detail_unreconcillied_in_init']
        date_from_dt = datetime.strptime(date_from, DEFAULT_SERVER_DATE_FORMAT) if date_from else False
        date_to_dt = datetime.strptime(date_to, DEFAULT_SERVER_DATE_FORMAT) if date_to else False
        date_init_dt = self._generate_date_init(date_from_dt)
        date_init = date_init_dt.strftime(DEFAULT_SERVER_DATE_FORMAT) if date_init_dt else False

        data['reconcile_clause'], data['matching_in_futur'], data['list_match_after_init'] = self._compute_reconcile_clause(type_ledger, data, date_init_dt)
        res = self._generate_sql(type_ledger, data, accounts, date_to, date_from)

        lines_group_by = {}
        group_by_ids = []
        group_by_field = D_LEDGER[type_ledger]['group_by']
        line_account = self._generate_account_dict(accounts)

        # for group_by, value in lines_group_by.items():
        init_lines_to_compact = []
        new_list = []
        for r in res:  # value['lines']:
            date_move_dt = datetime.strptime(r['date'], DEFAULT_SERVER_DATE_FORMAT)

            # Cas 1 : avant la date d'ouverture et, 401 non lettré avant la date d'ouverture
            #       si compte avec balance initiale
            #       -> pour calcul d'init
            #       sinon
            #       -> perdu
            # Cas 2 : entre la date d'ouverture et date_from, et 401 non lettré avant dat_to
            #       -> pour calcul d'init
            # Cas 3 : après la date_from
            #       -> pour affichage

            add_in = 'view'
            if with_init_balance:
                if r['a_type'] in ('payable', 'receivable') and detail_unreconcillied_in_init:
                    if not r['matching_number_id']:
                        matched_in_future = False
                        matched_after_init = False
                    else:
                        matched_after_init = True
                        matched_in_future = True
                        if r['matching_number_id'] in data['matching_in_futur']:
                            matched_in_future = False
                        if r['matching_number_id'] in data['list_match_after_init']:
                            matched_after_init = False
                else:
                    matched_after_init = True
                    matched_in_future = True

                if date_move_dt < date_init_dt and matched_after_init:
                    if r['include_initial_balance']:
                        add_in = 'init'
                    else:
                        add_in = 'not add'
                elif date_move_dt >= date_init_dt and date_from_dt and date_move_dt < date_from_dt and matched_in_future:
                    add_in = 'init'
                else:
                    add_in = 'view'

            r['reduce_balance'] = False
            if add_in == 'init':
                init_lines_to_compact.append(r)
                if r['a_type'] in ('payable', 'receivable') and date_move_dt < date_init_dt:
                    r['reduce_balance'] = True
            elif add_in == 'view':
                date_move = datetime.strptime(r['date'], DEFAULT_SERVER_DATE_FORMAT)
                r['date'] = date_move.strftime(date_format)
                r['date_maturity'] = datetime.strptime(r['date_maturity'], DEFAULT_SERVER_DATE_FORMAT).strftime(date_format)
                r['displayed_name'] = '-'.join(
                    r[field_name] for field_name in ('ref', 'name')
                    if r[field_name] not in (None, '', '/')
                )
                # if move is matching with the future then replace matching number par *
                if r['matching_number_id'] in data['matching_in_futur']:
                    r['matching_number'] = '*'

                r['type_line'] = 'normal'
                append_r = True if not type_ledger == 'open' else False
                if date_from_dt and date_move_dt < date_from_dt:
                    r['type_line'] = 'init'
                    r['code'] = 'INIT'
                    append_r = True

                if append_r:
                    new_list.append(r)

        init_balance_lines = self._generate_init_balance_lines(type_ledger, init_lines_to_compact, init_balance_history)

        if type_ledger == 'journal':
            all_lines = new_list
        else:
            all_lines = init_balance_lines + new_list

        for r in all_lines:
            if r[group_by_field] in lines_group_by.keys():
                lines_group_by[r[group_by_field]]['new_lines'].append(r)
            else:
                lines_group_by[r[group_by_field]] = {'new_lines': [r], }

        # remove unused group_by
        for group_by, value in lines_group_by.items():
            if not value['new_lines']:
                del lines_group_by[group_by]

        open_debit = 0
        open_credit = 0
        # compute sum by group_by
        # compute sum by account
        for group_by, value in lines_group_by.items():
            balance = 0.0
            sum_debit = 0.0
            sum_credit = 0.0
            for r in value['new_lines']:
                balance += r['debit'] - r['credit']
                r['progress'] = balance
                if float_is_zero(balance, rounding):
                    r['progress'] = 0.0

                sum_debit += r['debit']
                sum_credit += r['credit']
                open_debit += r['debit']
                open_credit += r['credit']

                r['s_debit'] = False if float_is_zero(r['debit'], rounding) else True
                r['s_credit'] = False if float_is_zero(r['credit'], rounding) else True

                line_account[r['account_id']]['debit'] += r['debit']
                line_account[r['account_id']]['credit'] += r['credit']
                line_account[r['account_id']]['active'] = True
                line_account[r['account_id']]['balance'] += r['debit'] - r['credit']

            balance = sum_debit - sum_credit
            if float_is_zero(balance, rounding):
                balance = 0.0

            if data['form']['sum_group_by_bottom']:
                lines_group_by[group_by]['new_lines'].append(self._generate_total(sum_debit, sum_credit, balance))

            lines_group_by[group_by]['s_debit'] = False if float_is_zero(sum_debit, rounding) else True
            lines_group_by[group_by]['s_credit'] = False if float_is_zero(sum_credit, rounding) else True
            lines_group_by[group_by]['debit - credit'] = balance
            lines_group_by[group_by]['debit'] = sum_debit
            lines_group_by[group_by]['credit'] = sum_credit

            group_by_ids.append(group_by)

        # remove unused account
        for key, value in line_account.items():
            if value['active'] == False:
                del line_account[key]

        open_balance = open_debit - open_credit
        if float_is_zero(open_balance, rounding):
            open_balance = 0.0

        open_data = {'debit': open_debit,
                     'credit': open_credit,
                     'balance': open_balance, }

        return lines_group_by, line_account, group_by_ids, open_data

    def _account(self, data):
        return data['line_account'].values()

    @api.multi
    def render_html(self, docis, data):
        lang_code = self.env.context.get('lang') or 'en_US'
        date_format = self.env['res.lang']._lang_get(lang_code).date_format
        type_ledger = data['form']['type_ledger']

        data['form']['name_report'] = self._get_name_report(data, type_ledger)

        data['computed'] = {}
        data['computed']['move_state'] = ['draft', 'posted']
        if data['form'].get('target_move', 'all') == 'posted':
            data['computed']['move_state'] = ['posted']

        accounts = self._search_account(data)
        obj_group_by = self.env[D_LEDGER[type_ledger]['model']]

        data['lines_group_by'], data['line_account'], group_by_ids, data['open_data'] = self._generate_data(type_ledger, data, accounts, date_format)

        group_by_data = obj_group_by.browse(group_by_ids)
        group_by_data = sorted(group_by_data, key=lambda x: x[D_LEDGER[type_ledger]['short']])

        data['form']['date_from'] = datetime.strptime(data['form']['date_from'], DEFAULT_SERVER_DATE_FORMAT).strftime(date_format) if data['form']['date_from'] else False
        data['form']['date_to'] = datetime.strptime(data['form']['date_to'], DEFAULT_SERVER_DATE_FORMAT).strftime(date_format) if data['form']['date_to'] else False

        docargs = {
            'group_by_top': self._group_by_top,
            'data': data,
            'docs': group_by_data,
            'time': time,
            'lines': self._lines,
            'sum_group_by': self._sum_group_by,
            'accounts': self._account,
        }
        return self.env['report'].render('account_standard_report.report_account_standard_report', docargs)

    def _lines(self, data, group_by):
        return data['lines_group_by'][group_by.id]['new_lines']

    def _sum_group_by(self, data, group_by, field):
        if field not in ['debit', 'credit', 'debit - credit', 's_debit', 's_credit']:
            return
        return data['lines_group_by'][group_by.id][field]

    def _group_by_top(self, data, group_by, field):
        type_ledger = data['form']['type_ledger']
        if type_ledger in ('general', 'journal', 'open'):
            code = group_by.code
            name = group_by.name
        elif type_ledger == 'partner':
            if group_by.ref:
                code = group_by.ref
                name = group_by.name
            else:
                code = group_by.name
                name = ''
        if field == 'code':
            return code or ''
        if field == 'name':
            return name or ''
        return

    def _search_account(self, data):
        type_ledger = data['form'].get('type_ledger')
        domain = [('deprecated', '=', False), ]
        if type_ledger == 'partner':
            result_selection = data['form'].get('result_selection', 'customer')
            if result_selection == 'supplier':
                acc_type = ['payable']
            elif result_selection == 'customer':
                acc_type = ['receivable']
            else:
                acc_type = ['payable', 'receivable']
            domain.append(('internal_type', 'in', acc_type))

        account_in_ex_clude = data['form'].get('account_in_ex_clude')
        acc_methode = data['form'].get('account_methode')
        if account_in_ex_clude:
            if acc_methode == 'include':
                domain.append(('id', 'in', account_in_ex_clude))
            elif acc_methode == 'exclude':
                domain.append(('id', 'not in', account_in_ex_clude))
        return self.env['account.account'].search(domain)

    def _compute_reconcile_clause(self, type_ledger, data, date_init):
        reconcile_clause = ""
        list_match_in_futur = []
        list_match_after_init = []

        if not data['form']['reconciled']:
            reconcile_clause = ' AND account_move_line.reconciled = false '

        # when an entrie a matching number and this matching number is linked with
        # entries witch the date is gretter than date_to, then
        # the entrie is considered like unreconciled.
        if data['form']['rem_futur_reconciled'] and data['form']['date_to']:
            date_to = datetime.strptime(data['form']['date_to'], DEFAULT_SERVER_DATE_FORMAT)
            acc_ful_obj = self.env['account.full.reconcile']

            def sql_query(params):
                query = """
                SELECT DISTINCT afr.id
                FROM account_full_reconcile afr
                INNER JOIN account_move_line aml ON aml.full_reconcile_id=afr.id
                AND aml.date > %s
                """
                self.env.cr.execute(query, params)
                return self.env.cr.dictfetchall()

            for r in sql_query([date_to]):
                list_match_in_futur.append(r['id'])
            for r in sql_query([date_init]):
                list_match_after_init.append(r['id'])

            if list_match_in_futur and not data['form']['reconciled']:
                if len(list_match_in_futur) == 1:
                    list_match_in_futur_sql = "(%s)" % (list_match_in_futur[0])
                else:
                    list_match_in_futur_sql = str(tuple(list_match_in_futur))
                reconcile_clause = ' AND (account_move_line.full_reconcile_id IS NULL OR account_move_line.full_reconcile_id IN ' + list_match_in_futur_sql + ')'

        return reconcile_clause, list_match_in_futur, list_match_after_init

    def _get_name_report(self, data, type_ledger):
        name = D_LEDGER[type_ledger]['name']
        if data['form']['summary']:
            name += ' Summary'
        return name

    def _generate_date_init(self, date_from_dt):
        if date_from_dt:
            last_day = self.env.user.company_id.fiscalyear_last_day or 31
            last_month = self.env.user.company_id.fiscalyear_last_month or 12
            if date_from_dt.month >= last_month and date_from_dt.day >= last_day:
                year = date_from_dt.year
            else:
                year = date_from_dt.year - 1
            return datetime(year=year, month=last_month, day=last_day) + timedelta(days=1)
        return False
