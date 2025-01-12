import asyncio

from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from loguru import logger

from web3 import AsyncWeb3
from web3.contract import AsyncContract
from web3.exceptions import ValidationError

from utils.params import token_addresses
from utils.utils import get_correct_amount_and_min_amount, get_token_decimals


async def send_token_chain_to_chain(
        wallet: str,
        from_chain_w3: AsyncWeb3,
        transaction_info: dict,
        stargate_from_chain_contract: AsyncContract,
        stargate_from_chain_address: ChecksumAddress,
        token_from_chain_contract: AsyncContract,
        from_chain_name: str,
        token: str,
        amount_to_swap: int,
        from_chain_explorer: str,
        gas: int
) -> HexBytes:
    """Send token from one blockchain to another. Tokens are sent to the same wallet.

    Args:
        wallet:                         Wallet address
        from_chain_w3:                  Client
        transaction_info:               Transaction info
        stargate_from_chain_contract:   Sending chain stargate router contract
        stargate_from_chain_address:    Address of Stargate Finance: Router at sending chain
        token_from_chain_contract:      Sending chain token contract
        from_chain_name:                Sending chain name
        token:                          Token symbol
        amount_to_swap:                 Human readable amount to swap
        from_chain_explorer:            Sending chain explorer
        gas:                            Amount of gas
    """
    account = from_chain_w3.eth.account.from_key(wallet)
    address = account.address

    nonce = await from_chain_w3.eth.get_transaction_count(address)
    gas_price = await from_chain_w3.eth.gas_price
    fees = await stargate_from_chain_contract.functions.quoteLayerZeroFee(
        transaction_info["chain_id"],  # uint16 _dstChainId
        1,  # uint8 _functionType
        "0x0000000000000000000000000000000000001010",  # bytes calldata _toAddress
        "0x",  # bytes calldata _transferAndCallPayload
        [0, 0, "0x0000000000000000000000000000000000000001"]  # Router.lz_tx_obj memory _lzTxParams
    ).call()
    fee = fees[0]

    allowance = await token_from_chain_contract.functions.allowance(address, stargate_from_chain_address).call()
    logger.debug(f"ALLOWANCE | {from_chain_name} Wallet {address} allowance for {token} is "
                 f"{allowance / 10 ** await get_token_decimals(token_from_chain_contract)}")

    if allowance < amount_to_swap:
        approve_txn = await token_from_chain_contract.functions.approve(
            stargate_from_chain_address,
            amount_to_swap
        ).build_transaction(
            {
                'from': address,
                'gas': 150000,
                'gasPrice': gas_price,
                'nonce': nonce,
            }
        )
        signed_approve_txn = from_chain_w3.eth.account.sign_transaction(approve_txn, wallet)
        approve_txn_hash = await from_chain_w3.eth.send_raw_transaction(signed_approve_txn.rawTransaction)

        logger.info(f"{from_chain_name} | {token} APPROVED https://{from_chain_explorer}/tx/{approve_txn_hash.hex()}")
        nonce += 1

        await asyncio.sleep(30)

    token_balance = await token_from_chain_contract.functions.balanceOf(address).call()

    if token_balance >= amount_to_swap:

        swap_txn = await stargate_from_chain_contract.functions.swap(
            transaction_info["chain_id"],
            transaction_info["source_pool_id"],
            transaction_info["dest_pool_id"],
            transaction_info["refund_address"],
            transaction_info["amount_in"],
            transaction_info["amount_out_min"],
            transaction_info["lz_tx_obj"],
            transaction_info["to"],
            transaction_info["data"]
        ).build_transaction(
            {
                'from': address,
                'value': fee,
                'gas': gas,
                'gasPrice': await from_chain_w3.eth.gas_price,
                'nonce': await from_chain_w3.eth.get_transaction_count(address),
            }
        )

        signed_swap_txn = from_chain_w3.eth.account.sign_transaction(swap_txn, wallet)
        swap_txn_hash = await from_chain_w3.eth.send_raw_transaction(signed_swap_txn.rawTransaction)
        return swap_txn_hash

    elif token_balance < amount_to_swap:

        try:
            _, min_amount = await get_correct_amount_and_min_amount(token_contract=token_from_chain_contract,
                                                                    amount_to_swap=token_balance)

            swap_txn = await stargate_from_chain_contract.functions.swap(
                transaction_info["chain_id"],
                transaction_info["source_pool_id"],
                transaction_info["dest_pool_id"],
                transaction_info["refund_address"],
                token_balance,
                min_amount,
                transaction_info["lz_tx_obj"],
                transaction_info["to"],
                transaction_info["data"]
            ).build_transaction(
                {
                    'from': address,
                    'value': fee,
                    'gas': gas,
                    'gasPrice': await from_chain_w3.eth.gas_price,
                    'nonce': await from_chain_w3.eth.get_transaction_count(address),
                }
            )

            signed_swap_txn = from_chain_w3.eth.account.sign_transaction(swap_txn, wallet)
            swap_txn_hash = await from_chain_w3.eth.send_raw_transaction(signed_swap_txn.rawTransaction)
            return swap_txn_hash

        except ValidationError as e:
            logger.error(f'Amount to be bridged is too low. Attempting raised an {e}')


async def check_balance(address: str, token: str, token_contract: AsyncContract) -> int:
    """Check token balance of the adress on the chain specified by the address.
    (USDC and USDT have different contracts of different blockchains.)

    Args:
        address:            wallet address
        token:              Token symbol
        token_contract:     token contract on a specified chain to interact with
    """
    token_balance = await token_contract.functions.balanceOf(address).call()
    logger.info(
        f'BALANCE | {token_addresses[token_contract.address.lower()]} Wallet {address} {token} balance is '
        f'{round(token_balance / 10 ** await get_token_decimals(token_contract), 2)}'
    )
    return token_balance


async def is_balance_updated(address: str, token: str, token_contract: AsyncContract) -> bool:
    """ Checks whether token balance on a specified chain is updated.
    (Is transfer completed or not)

    Args:
        address:            wallet address
        token:              Token symbol
        token_contract:     token contract on a specified chain to interact with
    """
    balance = await check_balance(address=address, token=token, token_contract=token_contract)

    while balance < 3 * (10 ** await get_token_decimals(token_contract)):
        await asyncio.sleep(10)
        balance = await check_balance(address=address, token=token, token_contract=token_contract)

    return True
