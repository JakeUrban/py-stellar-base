"""
SEP: 0010
Title: Stellar Web Authentication
Author: Sergey Nebolsin <sergey@mobius.network>, Tom Quisel <tom.quisel@gmail.com>, Leigh McCulloch <@leighmcculloch>
Status: Active
Created: 2018-07-31
Updated: 2019-12-04
Version 1.3.0
"""

import os
import time
from typing import List, Tuple

from .ed25519_public_key_signer import Ed25519PublicKeySigner
from .exceptions import InvalidSep10ChallengeError
from ..account import Account
from ..exceptions import BadSignatureError
from ..keypair import Keypair
from ..operation.manage_data import ManageData
from ..transaction_builder import TransactionBuilder
from ..transaction_envelope import TransactionEnvelope

__all__ = [
    "build_challenge_transaction",
    "verify_challenge_transaction_signers",
    "verify_challenge_transaction_signer",
    "verify_challenge_transaction_threshold",
    "read_challenge_transaction",
]





def build_challenge_transaction(
    server_secret: str,
    client_account_id: str,
    anchor_name: str,
    network_passphrase: str,
    timeout: int = 300,
) -> str:
    """Returns a valid `SEP0010 <https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0010.md>`_
    challenge transaction which you can use for Stellar Web Authentication.

    :param server_secret: secret key for server's signing account.
    :param client_account_id: The stellar account that the wallet wishes to authenticate with the server.
    :param anchor_name: Anchor's name to be used in the manage_data key.
    :param network_passphrase: The network to connect to for verifying and retrieving
        additional attributes from. (ex. 'Public Global Stellar Network ; September 2015')
    :param timeout: Challenge duration in seconds (default to 5 minutes).
    :return: A base64 encoded string of the raw TransactionEnvelope xdr struct for the transaction.
    """
    now = int(time.time())
    server_keypair = Keypair.from_secret(server_secret)
    server_account = Account(account_id=server_keypair.public_key, sequence=-1)
    transaction_builder = TransactionBuilder(server_account, network_passphrase, 100)
    transaction_builder.add_time_bounds(min_time=now, max_time=now + timeout)
    nonce = os.urandom(64)
    transaction_builder.append_manage_data_op(
        data_name="{} auth".format(anchor_name),
        data_value=nonce,
        source=client_account_id,
    )
    transaction = transaction_builder.build()
    transaction.sign(server_keypair)
    return transaction.to_xdr()


def read_challenge_transaction(
    challenge_transaction: str, server_account_id: str, network_passphrase: str
) -> Tuple[TransactionEnvelope, str]:
    """Reads a SEP 10 challenge transaction and returns the decoded transaction envelope and client account ID contained within..

    It also verifies that transaction is signed by the server.

    It does not verify that the transaction has been signed by the client or
    that any signatures other than the servers on the transaction are valid. Use
    one of the following functions to completely verify the transaction:
        - :func:`stellar_sdk.sep.stellar_web_authentication.verify_challenge_transaction_threshold`
        - :func:`stellar_sdk.sep.stellar_web_authentication.verify_challenge_transaction_signers`

    :param challenge_transaction: SEP0010 transaction challenge transaction in base64.
    :param server_account_id: public key for server's account.
    :param network_passphrase: The network to connect to for verifying and retrieving
        additional attributes from. (ex. 'Public Global Stellar Network ; September 2015')
    :raises: :exc:`InvalidSep10ChallengeError <stellar_sdk.sep.exceptions.InvalidSep10ChallengeError>` - if the
        validation fails, the exception will be thrown.
    """

    # decode the received input as a base64-urlencoded XDR representation of Stellar transaction envelope
    try:
        transaction_envelope = TransactionEnvelope.from_xdr(
            challenge_transaction, network_passphrase=network_passphrase
        )
    except Exception:
        raise InvalidSep10ChallengeError(
            "Importing XDR failed, please check if XDR is correct."
        )
    transaction = transaction_envelope.transaction

    # verify that transaction source account is equal to the server's signing key
    if transaction.source.public_key != server_account_id:
        raise InvalidSep10ChallengeError(
            "Transaction source account is not equal to server's account."
        )

    # verify that transaction has time bounds set, and that current time is between the minimum and maximum bounds
    if not transaction.time_bounds:
        raise InvalidSep10ChallengeError("Transaction requires timebounds.")

    max_time = transaction.time_bounds.max_time
    min_time = transaction.time_bounds.min_time

    if max_time == 0:
        raise InvalidSep10ChallengeError(
            "Transaction requires non-infinite timebounds."
        )

    current_time = time.time()
    if current_time < min_time or current_time > max_time:
        raise InvalidSep10ChallengeError(
            "Transaction is not within range of the specified timebounds."
        )

    # verify that transaction contains a single Manage Data operation and its source account is not null
    if len(transaction.operations) != 1:
        raise InvalidSep10ChallengeError(
            "Transaction requires a single ManageData operation."
        )

    manage_data_op = transaction.operations[0]
    if not isinstance(manage_data_op, ManageData):
        raise InvalidSep10ChallengeError("Operation type should be ManageData.")

    client_account_id = manage_data_op.source
    if not client_account_id:
        raise InvalidSep10ChallengeError("Operation should have a source account.")

    if len(manage_data_op.data_value) != 64:
        raise InvalidSep10ChallengeError(
            "Operation value should be a 64 bytes base64 random string."
        )

    if not transaction_envelope.signatures:
        raise InvalidSep10ChallengeError("Transaction has no signatures.")

    # verify that transaction envelope has a correct signature by server's signing key
    if not _verify_te_signed_by(transaction_envelope, server_account_id):
        raise InvalidSep10ChallengeError(
            "Transaction not signed by server: {}.".format(server_account_id)
        )

    # verify that transaction sequenceNumber is equal to zero
    if transaction.sequence != 0:
        raise InvalidSep10ChallengeError(
            "The transaction sequence number should be zero."
        )

    return transaction_envelope, client_account_id


def verify_challenge_transaction_signers(
    challenge_transaction: str,
    server_account_id: str,
    network_passphrase: str,
    signers: List[Ed25519PublicKeySigner],
) -> List[Ed25519PublicKeySigner]:
    """Verifies that for a SEP 10 challenge transaction
    all signatures on the transaction are accounted for. A transaction is
    verified if it is signed by the server account, and all other signatures
    match a signer that has been provided as an argument. Additional signers can
    be provided that do not have a signature, but all signatures must be matched
    to a signer for verification to succeed. If verification succeeds a list of
    signers that were found is returned, excluding the server account ID.

    :param challenge_transaction: SEP0010 transaction challenge transaction in base64.
    :param server_account_id: public key for server's account.
    :param network_passphrase: The network to connect to for verifying and retrieving
        additional attributes from. (ex. 'Public Global Stellar Network ; September 2015')
    :param signers: The signers of client account.

    :raises: :exc:`InvalidSep10ChallengeError <stellar_sdk.sep.exceptions.InvalidSep10ChallengeError>`:
        - The transaction is invalid according to :func:`stellar_sdk.sep.stellar_web_authentication.read_challenge_transaction`.
        - One or more signatures in the transaction are not identifiable as the server account or one of the signers provided in the arguments.
    """
    if not signers:
        raise InvalidSep10ChallengeError("No signers provided.")

    te, _ = read_challenge_transaction(
        challenge_transaction, server_account_id, network_passphrase
    )
    server_keypair = Keypair.from_public_key(server_account_id)

    # Remove the server signer from the signers list if it is present. It's
    # important when verifying signers of a challenge transaction that we only
    # verify and return client signers. If an account has the server as a
    # signer the server should not play a part in the authentication of the
    # client.
    client_signers = []
    for signer in signers:
        if signer == server_keypair.public_key:
            continue
        client_signers.append(signer)

    signers_found = _verify_transaction_signatures(te, client_signers)
    if len(signers_found) != len(te.signatures) - 1:
        raise InvalidSep10ChallengeError("Transaction has unrecognized signatures.")

    return signers_found


def verify_challenge_transaction_signer(
    challenge_transaction: str,
    server_account_id: str,
    network_passphrase: str,
    signer: Ed25519PublicKeySigner,
):
    """An alias for :func:`stellar_sdk.sep.stellar_web_authentication.verify_challenge_transaction_signers`.

    :param challenge_transaction: SEP0010 transaction challenge transaction in base64.
    :param server_account_id: public key for server's account.
    :param network_passphrase: The network to connect to for verifying and retrieving
        additional attributes from. (ex. 'Public Global Stellar Network ; September 2015')
    :param signer: The signer of client account.

    :raises: :exc:`InvalidSep10ChallengeError <stellar_sdk.sep.exceptions.InvalidSep10ChallengeError>`:
        - The transaction is invalid according to :func:`stellar_sdk.sep.stellar_web_authentication.read_challenge_transaction`.
        - One or more signatures in the transaction are not identifiable as the server account or one of the signers provided in the arguments.
    """
    return verify_challenge_transaction_signers(
        challenge_transaction, server_account_id, network_passphrase, [signer]
    )


def verify_challenge_transaction_threshold(
    challenge_transaction: str,
    server_account_id: str,
    network_passphrase: str,
    threshold: int,
    signers: List[Ed25519PublicKeySigner],
) -> List[Ed25519PublicKeySigner]:
    """Verifies that for a SEP 10 challenge transaction
    all signatures on the transaction are accounted for and that the signatures
    meet a threshold on an account. A transaction is verified if it is signed by
    the server account, and all other signatures match a signer that has been
    provided as an argument, and those signatures meet a threshold on the
    account.

    :param challenge_transaction: SEP0010 transaction challenge transaction in base64.
    :param server_account_id: public key for server's account.
    :param network_passphrase: The network to connect to for verifying and retrieving
        additional attributes from. (ex. 'Public Global Stellar Network ; September 2015')
    :param threshold: The medThreshold on the client account.
    :param signers: The signers of client account.

    :raises: :exc:`InvalidSep10ChallengeError <stellar_sdk.sep.exceptions.InvalidSep10ChallengeError>`:
        - The transaction is invalid according to :func:`stellar_sdk.sep.stellar_web_authentication.read_challenge_transaction`.
        - One or more signatures in the transaction are not identifiable as the server account or one of the signers provided in the arguments.
        - The signatures are all valid but do not meet the threshold.
    """
    signers_found = verify_challenge_transaction_signers(
        challenge_transaction, server_account_id, network_passphrase, signers
    )

    weight = 0
    for signer in signers_found:
        weight += signer.weight

    if weight < threshold:
        raise InvalidSep10ChallengeError(
            "signers with weight %d do not meet threshold %d." % (weight, threshold)
        )

    return signers_found


def verify_challenge_transaction(
    challenge_transaction: str, server_account_id: str, network_passphrase: str
) -> None:
    """Verifies if a transaction is a valid
    `SEP0010 <https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0010.md>`_
    challenge transaction, if the validation fails, an exception will be thrown.

    This function performs the following checks:

        1. verify that transaction sequenceNumber is equal to zero;
        2. verify that transaction source account is equal to the server's signing key;
        3. verify that transaction has time bounds set, and that current time is between the minimum and maximum bounds;
        4. verify that transaction contains a single Manage Data operation and it's source account is not null;
        5. verify that transaction envelope has a correct signature by server's signing key;
        6. verify that transaction envelope has a correct signature by the operation's source account;

    :param challenge_transaction: SEP0010 transaction challenge transaction in base64.
    :param server_account_id: public key for server's account.
    :param network_passphrase: The network to connect to for verifying and retrieving
        additional attributes from. (ex. 'Public Global Stellar Network ; September 2015')
    :raises: :exc:`InvalidSep10ChallengeError <stellar_sdk.sep.exceptions.InvalidSep10ChallengeError>` - if the
        validation fails, the exception will be thrown.
    """

    transaction_envelope, client_account_id = read_challenge_transaction(
        challenge_transaction, server_account_id, network_passphrase
    )

    if not _verify_te_signed_by(transaction_envelope, client_account_id):
        raise InvalidSep10ChallengeError(
            "Transaction not signed by client: {}.".format(client_account_id)
        )


def _verify_transaction_signatures(
    transaction_envelope: TransactionEnvelope, signers: List[Ed25519PublicKeySigner]
) -> List[Ed25519PublicKeySigner]:
    """Checks if a transaction has been signed by one or more of
    the signers, returning a list of signers that were found to have signed the
    transaction.

    :param transaction_envelope: SEP0010 transaction challenge transaction envelope.
    :param signers: The signers of client account.
    """
    signatures = transaction_envelope.signatures
    if not signatures:
        raise InvalidSep10ChallengeError("Transaction has no signatures.")

    signers_found = []
    for signer in signers:
        if _verify_te_signed_by(transaction_envelope, signer.account_id):
            signers_found.append(signer)
    return signers_found


def _verify_te_signed_by(
    transaction_envelope: TransactionEnvelope, account_id: str
) -> bool:
    kp = Keypair.from_public_key(account_id)
    for decorated_signature in transaction_envelope.signatures:
        try:
            kp.verify(transaction_envelope.hash(), decorated_signature.signature)
            return True
        except BadSignatureError:
            pass
    return False
