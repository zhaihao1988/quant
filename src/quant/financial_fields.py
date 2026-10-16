"""Explicit field dictionary and conservative canonical mapping; unknown IDs stay raw."""
OFFICIAL='https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10m001ic888.html'
MAPPING_VERSION='tdx-financial-v1'
# id -> (name, raw unit, applicability); monetary normalization is explicit.
FIELDS={
 1:('基本每股收益','元','common'),4:('每股净资产','元','common'),
 8:('货币资金','元','common'),11:('应收账款','元','common'),17:('存货','元','common'),
 21:('流动资产合计','元','common'),40:('资产总计','元','common'),54:('流动负债合计','元','common'),
 63:('负债合计','元','common'),64:('实收资本或股本','元','common'),69:('少数股东权益','元','common'),
 72:('所有者权益合计','元','common'),74:('其中营业收入','元','common'),75:('其中营业成本','元','common'),
 86:('营业利润','元','common'),95:('净利润','元','common'),96:('归母净利润','元','common'),
 97:('少数股东损益','元','common'),107:('经营活动现金流量净额','元','common'),
 119:('投资活动现金流量净额','元','common'),128:('筹资活动现金流量净额','元','common'),
 159:('流动比率','倍','non_financial'),160:('速动比率','倍','non_financial'),
 173:('存货周转率','次','non_financial'),202:('销售毛利率','%','non_financial'),
 206:('扣非净利润','元','common'),271:('归母股东权益','元','common'),
 304:('研发费用','元','common'),314:('财报公告日期','YYMMDD','common'),
 337:('股利支付率','%','common'),358:('主营业务收入（内销）','万元','common'),359:('主营业务收入（外销）','万元','common'),
 440:('发放贷款及垫款','万元','financial'),441:('应收款项','万元','securities'),
 442:('存出保证金','万元','securities'),443:('现金及存放中央银行款项','万元','financial'),
 448:('同业及其他金融机构存放款项','万元','financial'),450:('吸收存款','万元','financial'),
 502:('营业总收入','万元','common'),506:('利息收入','万元','common'),
 507:('已赚保费','万元','insurance'),508:('手续费及佣金收入','万元','common'),
 509:('利息支出','万元','common'),519:('营业总成本','万元','common'),
 523:('业务及管理费','万元','financial'),562:('客户存款和同业存放款项净增加额','万元','financial'),
 572:('客户贷款及垫款净增加额','万元','financial'),575:('支付利息手续费及佣金的现金','万元','financial'),
}
CANONICAL={
 'revenue_total':(502,10000),'net_profit_parent':(96,1),'equity_parent':(271,1),
 'assets_total':(40,1),'liabilities_total':(63,1),'equity_total':(72,1),
 'net_profit_total':(95,1),'cashflow_operating':(107,1),
}


def standard_facts(values):
    result={}
    for key,(field,multiplier) in CANONICAL.items():
        value=values.get(field)
        result[key]={'value':value*multiplier if value is not None else None,'field_id':field,
            'raw_unit':FIELDS[field][1],'unit':'元','status':'available' if value is not None else 'field_absent' if field not in values else 'missing'}
    return result


def company_profile(industry_names):
    names=set(industry_names)
    types={kind for kind,labels in {'bank':{'银行'},'securities':{'证券'},'insurance':{'保险'},
        'financial_other':{'多元金融','信托','期货','金融租赁'}}.items() if names&labels}
    if len(types)>1:return 'unknown'
    if types:return next(iter(types))
    return 'general' if names and not any('金融' in n or '银行' in n or '保险' in n or '证券' in n for n in names) else 'unknown'
