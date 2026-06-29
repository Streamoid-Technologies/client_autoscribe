import logging

logger = logging.getLogger(__name__)


def transform_term(exp):
    # A:a,b => A=a | A=b => [{A=a: true}, {A=b: true}] (OR)
    # !A:a,b => !A=a & !A=b => [{A=a: false, A=b: false}] (AND)
    out = []
    if exp['sign']:
        for val in exp['values']:
            tag = exp['attribute'] + ':' + val
            out.append({tag: True})
    else:
        out1 = {}
        for val in exp['values']:
            tag = exp['attribute'] + ':' + val
            out1[tag] = False
        out.append(out1)
    return out


def combine_exprs(expr1, expr2):
    out = []
    for exp1 in expr1:
        for exp2 in expr2:
            out1 = {}
            out1.update(exp1)
            out1.update(exp2)
            out.append(out1)
    return out


def combine_expr_list(expr_list):
    if len(expr_list) == 0:
        return []
    elif len(expr_list) == 1:
        return expr_list[0]
    elif len(expr_list) == 2:
        return combine_exprs(expr_list[0], expr_list[1])
    else:
        return combine_exprs(expr_list[0], combine_expr_list(expr_list[1:]))


def parse_condition(source_condition):
    tags = [x.strip() for x in source_condition.strip().split('+')]
    out = []
    for tag in tags:
        words = tag.strip().split(':')
        if len(words) != 2:
            return tag

        key = words[0].strip()
        values = [x.strip() for x in words[1].split(',')]

        key1 = key
        val = True
        if key.startswith('!'):
            key1 = key[1:]
            val = False
        out.append({'attribute': key1, 'values': values, 'sign': val})

    terms = [transform_term(x) for x in out]
    return combine_expr_list(terms)


def simplify_dict(expr):
    if len(expr) == 1:
        for k, v in expr.items():
            if v:
                return k


def simplify_list(expr):
    if len(expr) == 1:
        out = simplify_dict(expr[0])
        return out if out is not None else expr[0]


def simplify(expr):
    out = None
    if isinstance(expr, list):
        out = simplify_list(expr)
    elif isinstance(expr, dict):
        out = simplify_dict(expr)
    return out if out is not None else expr


def check_and(tags, condition):
    match = True
    for tag, val in condition.items():
        if (val and (tag not in tags)) or (not val and (tag in tags)):
            match = False
            break
    return match


def matches(tags, condition):
    if type(condition) == str:
        match = condition in tags
    elif type(condition) == dict:
        match = check_and(tags, condition)
    elif type(condition) == list:
        if len(condition) > 0:
            match = False
            for item in condition:
                if type(item) == str:
                    if item in tags:
                        match = True
                        break
                elif type(item) == dict:
                    if check_and(tags, item):
                        match = True
                        break
        else:
            match = True
    else:
        logger.error('Unknown condition type', condition)
        match = False

    return match


def check_condition_and(condition, tags):
    invalid = []
    for key in condition:
        if key not in tags:
            invalid.append(key)
    return invalid


def check_condition(condition, tags):
    invalid = []
    if type(condition) == str:
        if condition not in tags:
            invalid.append(condition)
    elif type(condition) == dict:
        invalid.extend(check_condition_and(condition, tags))
    elif type(condition) == list:
        for item in condition:
            invalid.extend(check_condition_and(item, tags))
    return list(set(invalid))
