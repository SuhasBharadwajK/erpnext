import frappe
from datetime import datetime, timedelta, timezone

FILTERS_KEY = "filters"
START_OFFSET_KEY = "start_offset"
END_OFFSET_KEY = "end_offset"

invoice_filters = {
    "all": {
        START_OFFSET_KEY: -1,
        END_OFFSET_KEY: -1
    },
    "30": {
        START_OFFSET_KEY: 0,
        END_OFFSET_KEY: 30,
    },
    "60": {
        START_OFFSET_KEY: 31,
        END_OFFSET_KEY: 60,
    },
    "90": {
        START_OFFSET_KEY: 61,
        END_OFFSET_KEY: 90,
    },
    "120": {
        START_OFFSET_KEY: 90,
        END_OFFSET_KEY: 120,
    },
    "oldest": {
        START_OFFSET_KEY: 121,
        END_OFFSET_KEY: -1,
    },
}

utc_now = datetime.now(timezone.utc)


def get_date_from_today(offset: int):
  return (utc_now + timedelta(days=offset)).strftime("%Y-%m-%d")


def get_filter_condition(offset: int, operator1: str, operator2: str):
  condition = ["due_date"]

  if offset == -1:
    condition.append(operator1)
  else:
    condition.append(operator2)

  date = get_date_from_today(offset)
  condition.append(date)

  return condition


def construct_filter(filter_param: str):
  start_offset = invoice_filters[filter_param][START_OFFSET_KEY]
  end_offset = invoice_filters[filter_param][END_OFFSET_KEY]

  filter_conditions = [
      [
          "is_paid",
          "=",
          "0",
      ]
  ]

  if start_offset == -1 and end_offset == -1:
    return filter_conditions

  gte_operator = ">="
  lte_operator = "<="

  start_conditions = get_filter_condition(
      start_offset,
      lte_operator,
      gte_operator
  )

  filter_conditions.append(start_conditions)

  end_conditions = get_filter_condition(
      end_offset,
      gte_operator,
      lte_operator
  )

  filter_conditions.append(end_conditions)

  return filter_conditions


@frappe.whitelist()
def get_invoices(filter_param: str, start: int, limit: int):
  try:
    # Use frappe.get_list to query the Purchase Invoice DocType
    invoices = frappe.get_list(
        "Purchase Invoice",
        fields=["*"],
        limit=10,
        order_by="due_date asc",
        filters=construct_filter(filter_param)  # invoice_filters[filter_param]
    )

    return invoices

  except KeyError as e:
    errmsg = "The given filter is not valid"
    frappe.errprint(errmsg)
    return {"error": errmsg}

  except Exception as e:
    frappe.log_error(frappe.get_traceback(), "Error fetching invoices")
    return {"error": str(e)}
