# 66 verified retailers — 44 VTEX + 15 Shopify + 7 Magento
STORES = {

    # ── SUPERMERCADOS ──
    "carrefour": {"name":"Carrefour AR","base":"https://www.carrefour.com.ar","country":"AR","currency":"ARS","emoji":"🇦🇷","line":"supermercados","platform":"vtex"},
    "vea_ar": {"name":"Vea AR","base":"https://www.vea.com.ar","country":"AR","currency":"ARS","emoji":"🇦🇷","line":"supermercados","platform":"vtex"},
    "jumbo_ar": {"name":"Jumbo AR","base":"https://www.jumbo.com.ar","country":"AR","currency":"ARS","emoji":"🇦🇷","line":"supermercados","platform":"vtex"},
    "carrefour_br": {"name":"Carrefour BR","base":"https://www.carrefour.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"supermercados","platform":"vtex"},
    "sams_club_br": {"name":"Sam's Club BR","base":"https://www.samsclub.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"supermercados","platform":"vtex"},
    "mambo_br": {"name":"Mambo BR","base":"https://www.mambo.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"supermercados","platform":"vtex"},
    "chedraui": {"name":"Chedraui","base":"https://www.chedraui.com.mx","country":"MX","currency":"MXN","emoji":"🇲🇽","line":"supermercados","platform":"vtex"},
    "heb_mx": {"name":"HEB","base":"https://www.heb.com.mx","country":"MX","currency":"MXN","emoji":"🇲🇽","line":"supermercados","platform":"vtex"},
    "exito": {"name":"Éxito","base":"https://www.exito.com","country":"CO","currency":"COP","emoji":"🇨🇴","line":"supermercados","platform":"vtex"},
    "carulla": {"name":"Carulla","base":"https://www.carulla.com","country":"CO","currency":"COP","emoji":"🇨🇴","line":"supermercados","platform":"vtex"},
    "olimpica": {"name":"Olímpica","base":"https://www.olimpica.com","country":"CO","currency":"COP","emoji":"🇨🇴","line":"supermercados","platform":"vtex"},
    "wong": {"name":"Wong","base":"https://www.wong.pe","country":"PE","currency":"PEN","emoji":"🇵🇪","line":"supermercados","platform":"vtex"},
    "metro": {"name":"Metro","base":"https://www.metro.pe","country":"PE","currency":"PEN","emoji":"🇵🇪","line":"supermercados","platform":"vtex"},
    "plazavea": {"name":"Plaza Vea","base":"https://www.plazavea.com.pe","country":"PE","currency":"PEN","emoji":"🇵🇪","line":"supermercados","platform":"vtex"},

    # ── FARMACIAS ──
    "pacheco_br": {"name":"Drogaria Pacheco","base":"https://www.drogariaspacheco.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"farmacias","platform":"vtex"},
    "globo_br": {"name":"Drogaria Globo","base":"https://www.drogariaglobo.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"farmacias","platform":"vtex"},
    "farmatodo_mx": {"name":"Farmatodo MX","base":"https://www.farmatodo.com.mx","country":"MX","currency":"MXN","emoji":"🇲🇽","line":"farmacias","platform":"vtex"},
    "cruzverde_co": {"name":"Cruz Verde CO","base":"https://www.cruzverde.com.co","country":"CO","currency":"COP","emoji":"🇨🇴","line":"farmacias","platform":"vtex","disabled":True,"disabled_reason":"search API returns HTML SPA"},
    "cruzverde_cl": {"name":"Cruz Verde CL","base":"https://www.cruzverde.cl","country":"CL","currency":"CLP","emoji":"🇨🇱","line":"farmacias","platform":"vtex","disabled":True,"disabled_reason":"search API returns HTML SPA"},
    "farmatodo_co": {"name":"Farmatodo CO","base":"https://www.farmatodo.com.co","country":"CO","currency":"COP","emoji":"🇨🇴","line":"farmacias","platform":"vtex","disabled":True,"disabled_reason":"search API returns HTML SPA"},

    # ── ELECTRO ──
    "electrolux_ar": {"name":"Electrolux AR","base":"https://www.tienda.electrolux.com.ar","country":"AR","currency":"ARS","emoji":"🇦🇷","line":"electro","platform":"vtex"},
    "motorola_ar": {"name":"Motorola AR","base":"https://www.motorola.com.ar","country":"AR","currency":"ARS","emoji":"🇦🇷","line":"electro","platform":"vtex"},
    "whirlpool_ar": {"name":"Whirlpool AR","base":"https://www.whirlpool.com.ar","country":"AR","currency":"ARS","emoji":"🇦🇷","line":"electro","platform":"vtex"},
    "motorola_br": {"name":"Motorola BR","base":"https://www.motorola.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"electro","platform":"vtex"},
    "motorola_mx": {"name":"Motorola MX","base":"https://www.motorola.com.mx","country":"MX","currency":"MXN","emoji":"🇲🇽","line":"electro","platform":"vtex"},
    "electrolux_cl": {"name":"Electrolux CL","base":"https://www.electrolux.cl","country":"CL","currency":"CLP","emoji":"🇨🇱","line":"electro","platform":"vtex"},
    "motorola_cl": {"name":"Motorola CL","base":"https://www.motorola.cl","country":"CL","currency":"CLP","emoji":"🇨🇱","line":"electro","platform":"vtex"},
    "whirlpool_it": {"name":"Whirlpool IT","base":"https://www.whirlpool.it","country":"IT","currency":"EUR","emoji":"🇮🇹","line":"electro","platform":"vtex"},
    "whirlpool_fr": {"name":"Whirlpool FR","base":"https://www.whirlpool.fr","country":"FR","currency":"EUR","emoji":"🇫🇷","line":"electro","platform":"vtex"},
    "motorola_es": {"name":"Motorola ES","base":"https://www.motorola.es","country":"ES","currency":"EUR","emoji":"🇪🇸","line":"electro","platform":"vtex","disabled":True,"enable_with_credentials":True,"disabled_reason":"VTEX catalog requires app credentials"},
    "electrolux_mx": {"name":"Electrolux MX","base":"https://www.electrolux.com.mx","country":"MX","currency":"MXN","emoji":"🇲🇽","line":"electro","platform":"vtex","disabled":True,"enable_with_credentials":True,"disabled_reason":"VTEX catalog requires app credentials"},
    "samsung_br": {"name":"Samsung BR","base":"https://www.samsung.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"electro","platform":"vtex","disabled":True,"enable_with_credentials":True,"disabled_reason":"VTEX catalog requires app credentials"},
    "samsung_mx": {"name":"Samsung MX","base":"https://www.samsung.com.mx","country":"MX","currency":"MXN","emoji":"🇲🇽","line":"electro","platform":"vtex","disabled":True,"enable_with_credentials":True,"disabled_reason":"VTEX catalog requires app credentials"},
    "oster_br": {"name":"Oster BR","base":"https://www.oster.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"electro","platform":"vtex"},

    # ── HOGAR ──
    "easy_ar": {"name":"Easy AR","base":"https://www.easy.com.ar","country":"AR","currency":"ARS","emoji":"🇦🇷","line":"hogar","platform":"vtex"},
    "promart": {"name":"Promart","base":"https://www.promart.pe","country":"PE","currency":"PEN","emoji":"🇵🇪","line":"hogar","platform":"vtex"},

    # ── DEPARTAMENTALES ──
    "coppel_ar": {"name":"Coppel AR","base":"https://www.coppel.com.ar","country":"AR","currency":"ARS","emoji":"🇦🇷","line":"departamentales","platform":"vtex"},
    "rihappy_br": {"name":"Ri Happy","base":"https://www.rihappy.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"departamentales","platform":"vtex"},
    "ripley_pe": {"name":"Ripley PE","base":"https://www.ripley.pe","country":"PE","currency":"PEN","emoji":"🇵🇪","line":"departamentales","platform":"vtex","disabled":True,"enable_with_credentials":True,"disabled_reason":"VTEX catalog API 404 without credentials"},

    # ── MODA ──
    "cea_br": {"name":"C&A BR","base":"https://www.cea.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"moda","platform":"vtex"},
    "hering_br": {"name":"Hering BR","base":"https://www.hering.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"moda","platform":"vtex"},
    "aramis_br": {"name":"Aramis","base":"https://www.aramis.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"moda","platform":"vtex"},
    "miess_br": {"name":"Miess","base":"https://www.miess.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"moda","platform":"vtex"},
    "decathlon_br": {
        "name": "Decathlon BR",
        "base": "https://decathlonstore.vtexcommercestable.com.br",
        "link_base": "https://www.decathlon.com.br",
        "country": "BR",
        "currency": "BRL",
        "emoji": "🇧🇷",
        "line": "moda",
        "platform": "vtex",
    },

    # ── SHOPIFY (POC) ──
    "gymshark": {"name":"Gymshark","base":"https://www.gymshark.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"moda","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "allbirds": {"name":"Allbirds","base":"https://www.allbirds.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"moda","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "colourpop": {"name":"ColourPop","base":"https://colourpop.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"moda","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "adidas": {"name":"Adidas","base":"https://www.adidas.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"moda","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "alo_yoga": {"name":"Alo Yoga","base":"https://www.aloyoga.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"moda","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "glossier": {"name":"Glossier","base":"https://www.glossier.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"moda","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "fenty": {"name":"Fenty Beauty","base":"https://fentybeauty.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"moda","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "kylie": {"name":"Kylie Cosmetics","base":"https://kyliecosmetics.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"moda","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "brooklinen": {"name":"Brooklinen","base":"https://www.brooklinen.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"hogar","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "parachute": {"name":"Parachute","base":"https://www.parachutehome.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"hogar","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "casper": {"name":"Casper","base":"https://casper.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"hogar","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "nomad": {"name":"Nomad","base":"https://nomadgoods.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"electro","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "magicmind": {"name":"Magic Mind","base":"https://www.magicmind.com","country":"US","currency":"USD","emoji":"🇺🇸","line":"supermercados","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "on_running": {"name":"On Running","base":"https://www.on.com","country":"CH","currency":"CHF","emoji":"🇨🇭","line":"moda","platform":"shopify","disabled":True,"enable_with_credentials":True},
    "privalia_br": {"name":"Privalia BR","base":"https://www.privalia.com.br","country":"BR","currency":"BRL","emoji":"🇧🇷","line":"moda","platform":"shopify","disabled":True,"enable_with_credentials":True},

    # ── MAGENTO ──
    "falabella_pe": {"name":"Falabella PE","base":"https://www.falabella.com.pe","country":"PE","currency":"PEN","emoji":"🇵🇪","line":"departamentales","platform":"magento","disabled":True,"enable_with_credentials":True,"disabled_reason":"Magento REST requires token"},
    "falabella_cl": {"name":"Falabella CL","base":"https://www.falabella.com","country":"CL","currency":"CLP","emoji":"🇨🇱","line":"departamentales","platform":"magento","disabled":True,"enable_with_credentials":True,"disabled_reason":"Magento REST requires token"},
    "falabella_co": {"name":"Falabella CO","base":"https://www.falabella.com.co","country":"CO","currency":"COP","emoji":"🇨🇴","line":"departamentales","platform":"magento","disabled":True,"enable_with_credentials":True,"disabled_reason":"Magento REST requires token"},
    "paris_cl": {"name":"Paris CL","base":"https://www.paris.cl","country":"CL","currency":"CLP","emoji":"🇨🇱","line":"departamentales","platform":"magento","disabled":True,"enable_with_credentials":True,"disabled_reason":"Magento REST requires token"},
    "ripley_cl": {"name":"Ripley CL","base":"https://www.ripley.cl","country":"CL","currency":"CLP","emoji":"🇨🇱","line":"departamentales","platform":"magento","disabled":True,"enable_with_credentials":True,"disabled_reason":"Magento REST requires token"},
    "liverpool_mx": {"name":"Liverpool MX","base":"https://www.liverpool.com.mx","country":"MX","currency":"MXN","emoji":"🇲🇽","line":"departamentales","platform":"magento","disabled":True,"disabled_reason":"Magento REST blocked (403)"},
    "elpalacio_mx": {"name":"El Palacio MX","base":"https://www.elpalaciodehierro.com","country":"MX","currency":"MXN","emoji":"🇲🇽","line":"departamentales","platform":"magento","disabled":True,"disabled_reason":"Magento REST 410 Gone"},
}

