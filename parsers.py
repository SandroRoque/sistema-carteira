
def br_date_parser(date_str):
    """Parses a date string in the format 'DD/MM/YYYY' and returns a datetime.date object."""
    from datetime import datetime
    try:
        return datetime.strptime(date_str, "%d/%m/%Y").date()
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str}. Expected format is 'DD/MM/YYYY'.")


def br_number_parser(number_str):
    """Parses a Brazilian-formatted number string and returns a float."""
    cleaned_input = number_str.strip()
    if cleaned_input in {"", "-"}:
        return None

    try:
        is_percent = cleaned_input.endswith("%")
        if is_percent:
            cleaned_input = cleaned_input[:-1].strip()

        if is_percent and "." in cleaned_input and "," not in cleaned_input:
            cleaned_str = cleaned_input
        else:
            cleaned_str = cleaned_input.replace(".", "").replace(",", ".")

        return float(cleaned_str)
    except ValueError:
        raise ValueError(
            f"Invalid number format: {number_str}. Expected format is like '1.234,56'."
        )


def percent_parser(percent_str):
    """Parses a percentage string like '110.50 %' and returns a float."""
    cleaned_input = percent_str.strip()
    if cleaned_input in {"", "-"}:
        return None

    try:
        if cleaned_input.endswith("%"):
            cleaned_input = cleaned_input[:-1].strip()

        if "." in cleaned_input and "," not in cleaned_input:
            cleaned_str = cleaned_input
        else:
            cleaned_str = cleaned_input.replace(".", "").replace(",", ".")

        return float(cleaned_str)
    except ValueError:
        raise ValueError(
            f"Invalid percent format: {percent_str}. Expected format is like '110.50 %'."
        )


def money_parser(money_str):
    """Parses a money string in the format '1.234,56' and returns a float."""
    cleaned_input = money_str.strip()
    if cleaned_input in {"", "-"}:
        return None

    try:
        if cleaned_input.startswith("R$"):
            cleaned_input = cleaned_input[2:].strip()

        cleaned_input = cleaned_input.replace("|", " ").strip()

        tokens = cleaned_input.split()
        sign = 1.0
        if tokens and tokens[-1] in {"C", "D"}:
            if tokens[-1] == "D":
                sign = -1.0
            cleaned_input = " ".join(tokens[:-1]).strip()

        if cleaned_input in {"", "-"}:
            return None

        # Remove thousand separators, then replace decimal comma with dot
        cleaned_str = cleaned_input.replace(".", "").replace(",", ".")
        return float(cleaned_str) * sign
    except ValueError:
        raise ValueError(f"Invalid money format: {money_str}. Expected format is '1.234,56'.")


def int_parser(int_str):
    """Parses a string representing an integer and returns an int."""
    try:
        return int(int_str.strip())
    except ValueError:
        raise ValueError(f"Invalid integer format: {int_str}. Expected a valid integer string.")


def cpf_parser(cpf_str):
    """Normaliza um CPF removendo pontuacao e validando se possui 11 digitos."""
    digits_only = "".join(char for char in cpf_str if char.isdigit())

    if len(digits_only) != 11:
        raise ValueError(f"Invalid CPF format: {cpf_str}. Expected 11 digits.")

    return f"{digits_only[:3]}.{digits_only[3:6]}.{digits_only[6:9]}-{digits_only[9:]}"
