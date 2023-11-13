import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

import braintree
from django.db import models
from django.test import TestCase
from factory import Faker, LazyAttribute, Sequence, base
from money import Money

logger = logging.getLogger(__name__)


class CURRENCY_MERCHANT_ACCOUNT_MAP:
    USD = 'prose-usd'
    CAD = 'prose-cad'


class PaymentClientError(Exception):
    def __init__(self, message=None, user_message=None, *args, **kwargs):
        super().__init__(message, *args, **kwargs)


@dataclass
class PaypalPaymentInfoDataClass:
    email: str
    name: str = None


@dataclass
class DeletedObjectDataClass:
    id: str
    deleted: bool
    object: str


class PaymentModes(models.TextChoices):
    STRIPE_CHARGE = 'stripe_charge', 'Stripe Charge API'
    STRIPE_PAYMENT_INTENT = 'stripe_payment_intent', 'Stripe Payment Intent API'
    BRAINTREE = 'braintree', 'Braintree API'


class Customer:
    id = models.AutoField(primary_key=True, db_index=True)
    pubkey = models.UUIDField(default=uuid4, db_index=True, editable=False, unique=True)
    username = models.EmailField(max_length=255, unique=True)
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=255, blank=True, null=True)

    def __init__(self, *args, **kwargs):
        for field, value in kwargs.items():
            setattr(self, field, value)


class CustomerFactory(base.Factory):
    id = Sequence(lambda n: n + 100000)
    pubkey = Faker('uuid4')
    username = LazyAttribute(lambda obj: f'customer-{obj.pubkey}@prosehair.test.com')
    first_name = Faker('first_name_nonbinary')
    last_name = Faker('last_name')
    phone = Faker('phone_number')

    class Meta:
        model = Customer


class BraintreeClient:
    def __init__(self):
        self.gateway = braintree.BraintreeGateway(
            braintree.Configuration(
                environment=braintree.Environment.Sandbox,
                merchant_id='znqjm7gc8nv6q3g9',
                public_key='4y6mbj59fr3qyzb6',
                private_key='0aa5ad759873dd453d3485cb962f4f7c',
            )
        )

    def get_token(self, customer_pubkey: str) -> str:
        try:
            client_token = self.gateway.client_token.generate({'customer_id': str(customer_pubkey)})
            return client_token
        except Exception as err:
            logger.error(f'Error getting token: {str(err)}', extra={'err__dict': err.__dict__})
            raise err

    def create_customer(self, **kwargs) -> str:
        try:
            result = self.gateway.customer.create(kwargs)
            return result.customer.id
        except Exception as err:
            logger.error(f'Error creating customer: {str(err)}', extra={'err__dict': err.__dict__})
            raise err

    def retrieve_customer(self, customer_id) -> str:
        try:
            braintree_customer = self.gateway.customer.find(customer_id)
            return braintree_customer.id
        except Exception as err:
            logger.warning(f'Error retrieving customer: {str(err)}', extra={'err__dict': err.__dict__})
        return None

    def delete_customer(self, customer_id) -> 'DeletedObjectDataClass':
        try:
            deleted_customer = self.gateway.customer.delete(customer_id)
            return DeletedObjectDataClass(id=customer_id, deleted=deleted_customer.is_success, object='customer')
        except Exception as err:
            logger.error(f'Error deleting customer: {str(err)}', extra={'err__dict': err.__dict__})
            raise err

    def refund_payment(self, refund_kwargs, order_total_price) -> str:
        try:
            refund = self.gateway.transaction.refund(refund_kwargs['transaction_id'], refund_kwargs.get('refund_data'))
            if refund.is_success is False and refund.errors.deep_errors and refund.errors.deep_errors[0].code == '91506':
                if refund_kwargs.get('refund_data', {}).get('amount'):
                    # A logic to void a transaction and create a new one with the new amount is needed
                    logger.error('Trying to refund a partial amount for an authorized or submitted for settlement transaction')
                    raise NotImplementedError('Refund for authorized and submitted for settlement transactions is not supported')
                else:
                    voided = self.gateway.transaction.void(refund_kwargs['transaction_id'])  # Full refund, void the transaction
                    if voided.is_success is False:
                        raise PaymentClientError(message=voided.message)
                    return voided.transaction.id
            return refund.transaction.id
        except Exception as e:
            logger.error(f'Error refunding payment: {str(e)}', extra={'err__dict': e.__dict__})
            raise e

    def create_payment_mode(self, payment_source_id, payment_mode_kwargs: dict) -> str:
        try:
            sale = self.gateway.transaction.sale(payment_mode_kwargs)
            if not sale.is_success:
                raise PaymentClientError(message=sale.message)
            return sale.transaction.id
        except Exception as err:
            logger.error(f'Error creating payment mode: {str(err)}', extra={'err__dict': err.__dict__})
            raise err

    def get_payment_source_info(self, payment_mode_id) -> 'PaypalPaymentInfoDataClass':
        transaction = self.gateway.transaction.find(payment_mode_id)
        return PaypalPaymentInfoDataClass(
            email=transaction.paypal_details.payer_email,
            name=transaction.paypal_details.payer_first_name + ' ' + transaction.paypal_details.payer_last_name,
        )


class BraintreeClientTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.braintree_client = BraintreeClient()
        super().setUpTestData()

    def _get_customer_creation_payload(self, customer: Customer):
        customer_creation_payload = {
            'id': str(customer.pubkey),
            'first_name': customer.first_name,
            'last_name': customer.last_name,
            'email': customer.username,
            'phone': customer.phone,
        }
        return customer_creation_payload

    def _create_customer(self):
        customer = CustomerFactory.build()
        customer_creation_payload = self._get_customer_creation_payload(customer)
        self.braintree_client.create_customer(**customer_creation_payload)
        return customer

    def test_create_customer(self):
        """
        Given a customer
        When create_customer is called
        Then a customer is created
        """
        customer = CustomerFactory.build()
        customer_creation_payload = self._get_customer_creation_payload(customer)
        customer_id = self.braintree_client.create_customer(**customer_creation_payload)
        self.assertIsNotNone(customer_id)
        braintree_customer = self.braintree_client.gateway.customer.find(customer_id)
        self.assertIsNotNone(braintree_customer)
        self.assertEqual(braintree_customer.email, customer.username)

    def test_get_token(self):
        """
        Given a customer
        When get_token is called after creating a customer in Braintree
        Then a token is returned
        """
        customer = self._create_customer()
        token = self.braintree_client.get_token(customer.pubkey)
        self.assertIsNotNone(token)

    def test_get_token_no_customer(self):
        """
        Given a customer
        When get_token is called without creating a customer in Braintree
        Then a PaymentClientError is raised
        """
        customer = CustomerFactory.build()
        with self.assertRaises(ValueError) as e:
            self.braintree_client.get_token(customer.pubkey)
        self.assertEqual(str(e.exception), 'Customer specified by customer_id does not exist')

    def test_retrieve_customer(self):
        """
        Given a customer
        When retrieve_customer is called after creating a customer in Braintree
        Then a customer is returned
        """
        customer = self._create_customer()
        customer_id = self.braintree_client.retrieve_customer(customer.pubkey)
        self.assertIsNotNone(customer_id)
        self.assertEqual(customer_id, str(customer.pubkey))

    def test_retrieve_customer_not_created(self):
        """
        Given a customer
        When retrieve_customer is called without creating a customer in Braintree
        Then None is returned
        """
        customer = CustomerFactory.build()
        with self.assertLogs('prose.test_braintree_lite', level='WARNING') as cm:
            customer_id = self.braintree_client.retrieve_customer(customer.pubkey)
        self.assertIsNone(customer_id)
        self.assertEqual(
            cm.output,
            [
                f"WARNING:prose.test_braintree_lite:Error retrieving customer: customer with id '{str(customer.pubkey)}' not found",
            ],
        )

    def test_delete_customer(self):
        """
        Given a customer
        When delete_customer is called after creating a customer in Braintree
        Then the customer is deleted
        """
        customer = self._create_customer()
        deleted_customer = self.braintree_client.delete_customer(customer.pubkey)
        self.assertTrue(deleted_customer.deleted)
        self.assertEqual(deleted_customer.id, str(customer.pubkey))

        with self.assertRaises(braintree.exceptions.not_found_error.NotFoundError) as e:
            self.braintree_client.gateway.customer.find(customer.pubkey)
            self.assertEqual(str(e.exception), f"customer with id '{str(customer.pubkey)}' not found")

    def _assert_customer_transactions_values(self, customer, expected_transactions_values: 'list[tuple[Money, str]]'):
        transactions = self.braintree_client.gateway.transaction.search(braintree.TransactionSearch.customer_id == customer.pubkey)
        self.assertEqual([(Money(t.amount, t.currency_iso_code), t.status) for t in transactions.items], expected_transactions_values)

    def test_create_payment(self):
        """
        Given a customer
        When create_payment is called
        Then a payment is created in Braintree
        """
        order_id = str(uuid4())
        customer = self._create_customer()
        sale_options = {
            'amount': '100',
            'device_data': {},
            'options': {
                'submit_for_settlement': True,
                'store_in_vault_on_success': True,
            },
            'order_id': order_id,
            'merchant_account_id': CURRENCY_MERCHANT_ACCOUNT_MAP.USD,
            'customer_id': str(customer.pubkey),
            'payment_method_nonce': 'fake-valid-nonce',
            'transaction_source': 'recurring_first',
        }
        sale = self.braintree_client.create_payment_mode(None, sale_options)
        self.assertIsNotNone(sale)

        transaction = self.braintree_client.gateway.transaction.find(sale)
        self.assertIsNotNone(transaction)
        self.assertEqual(transaction.order_id, order_id)
        self.assertEqual(transaction.customer_details.id, str(customer.pubkey))
        self.assertEqual(transaction.status, 'submitted_for_settlement')
        self.assertEqual(transaction.amount, Decimal('100'))
        self.assertEqual(transaction.currency_iso_code, 'USD')

        self._assert_customer_transactions_values(customer, [(Money('100', 'USD'), 'submitted_for_settlement')])

    # @skip('This test is failing because Braintree returns success even if the Nonce is invalid. Waiting response from Braintree.')
    def test_create_payment_insufficient_funds(self):
        """
        Given a customer
        When create_payment is called with an amount greater than the customer's balance
        Then a PaymentClientError is raised
        """
        order_id = str(uuid4())
        customer = self._create_customer()
        sale_options = {
            'amount': '100',
            'device_data': {},
            'options': {
                'store_in_vault_on_success': True,
            },
            'order_id': order_id,
            'merchant_account_id': CURRENCY_MERCHANT_ACCOUNT_MAP.USD,
            'customer_id': str(customer.pubkey),
            'payment_method_nonce': 'fake-processor-declined-visa-nonce',
            'transaction_source': 'recurring_first',
        }
        with self.assertRaises(PaymentClientError) as e:
            self.braintree_client.create_payment_mode(None, sale_options)
        self.assertEqual(
            str(e.exception),
            'Do Not Honor - Insufficient Funds: The transaction was declined due to insufficient funds in your account. Please use a different card or contact your bank.',  # noqa: E501
        )

        self._assert_customer_transactions_values(customer, [(Money('100', 'USD'), 'processor_declined')])

    def test_create_payment_idempotency(self):
        """
        Given a customer
        When create_payment is called twice with the same order_id
        Then the second call raises a PaymentClientError
        """
        order_id = str(uuid4())
        customer = self._create_customer()
        sale_options = {
            'amount': '100',
            'device_data': {},
            'options': {
                'submit_for_settlement': True,
                'store_in_vault_on_success': True,
            },
            'order_id': order_id,
            'merchant_account_id': CURRENCY_MERCHANT_ACCOUNT_MAP.USD,
            'customer_id': str(customer.pubkey),
            'payment_method_nonce': 'fake-valid-nonce',
            'transaction_source': 'recurring_first',
        }
        sale1 = self.braintree_client.create_payment_mode(None, sale_options)
        self.assertIsNotNone(sale1)

        transaction1 = self.braintree_client.gateway.transaction.find(sale1)
        self.assertIsNotNone(transaction1)
        self.assertEqual(transaction1.order_id, order_id)
        self.assertEqual(transaction1.customer_details.id, str(customer.pubkey))
        self.assertEqual(transaction1.status, 'submitted_for_settlement')
        self.assertEqual(transaction1.amount, Decimal('100'))
        self.assertEqual(transaction1.currency_iso_code, 'USD')

        with self.assertRaises(PaymentClientError) as e:
            self.braintree_client.create_payment_mode(None, sale_options)
        self.assertEqual(str(e.exception), 'Gateway Rejected: duplicate')

        self._assert_customer_transactions_values(
            customer,
            [
                (Money('100', 'USD'), 'gateway_rejected'),
                (Money('100', 'USD'), 'submitted_for_settlement'),
            ],
        )

    # @skip(
    #     'This test is failing because transaction is in submitted_for_settlement status and refund is not authorized. Waiting response from Braintree how to put it as settled.'  # noqa: E501
    # )
    def test_refund_payment(self):
        """
        Given a customer
        When refund_payment for a partial refund is called
        Then a refund is created in Braintree
        """
        customer = self._create_customer()
        order_id = str(uuid4())
        sale_options = {
            'amount': '100',
            'device_data': {},
            'options': {
                'submit_for_settlement': True,
                'store_in_vault_on_success': True,
            },
            'order_id': order_id,
            'merchant_account_id': CURRENCY_MERCHANT_ACCOUNT_MAP.USD,
            'customer_id': str(customer.pubkey),
            'payment_method_nonce': 'fake-valid-nonce',
            'transaction_source': 'recurring_first',
        }
        sale_id = self.braintree_client.create_payment_mode(None, sale_options)
        refund_payload = {'transaction_id': sale_id, 'refund_data': {'amount': '25.00'}}
        refund_id = self.braintree_client.refund_payment(refund_payload, Money('25.00', 'USD'))
        self.assertIsNotNone(refund_id)

        self._assert_customer_transactions_values(
            customer,
            [
                (Money('100', 'USD'), 'submitted_for_settlement'),
                (Money('-25', 'USD'), 'submitted_for_settlement'),
            ],
        )

    def test_refund_payment_full_refund_void(self):
        """
        Given a submitted for settlement transaction
        When refund_payment for a full refund is called
        Then the transaction is voided
        """
        customer = self._create_customer()
        order_id = str(uuid4())
        sale_options = {
            'amount': '100',
            'device_data': {},
            'options': {
                'submit_for_settlement': True,
                'store_in_vault_on_success': True,
            },
            'order_id': order_id,
            'merchant_account_id': CURRENCY_MERCHANT_ACCOUNT_MAP.USD,
            'customer_id': str(customer.pubkey),
            'payment_method_nonce': 'fake-valid-nonce',
            'transaction_source': 'recurring_first',
        }
        sale_id = self.braintree_client.create_payment_mode(None, sale_options)

        self._assert_customer_transactions_values(customer, [(Money('100', 'USD'), 'submitted_for_settlement')])

        refund_payload = {'transaction_id': sale_id}
        voided_id = self.braintree_client.refund_payment(refund_payload, Money('100.00', 'USD'))
        self.assertIsNotNone(voided_id)

        self._assert_customer_transactions_values(customer, [(Money('100', 'USD'), 'voided')])
