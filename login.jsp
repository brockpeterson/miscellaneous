<%@ taglib prefix="s" uri="/WEB-INF/struts-tags.tld" %>
<%@ taglib uri="http://java.sun.com/jsp/jstl/functions" prefix="fn" %>
<s:include value="mainTop.jsp">
    <s:param name="scripts">
        <script type="text/javascript">
            const urlParams = new URLSearchParams(window.location.search);
            const vcfLoginMode = (urlParams.get('vcf') == 1);
            if ((${vcfMode} && !vcfLoginMode) || window != parent) {
                let uriParam = '';
                const hash = window.location.hash.substring(1);
                if (hash.length > 0) {
                    const basePath = 'vcf-operations/ui';
                    const legacyParams = new URLSearchParams({
                        service: 'ops',
                        path: hash
                    });
                    const legacyPath = basePath + '/legacy?' + legacyParams;
                    uriParam = '&uri=' + btoa(legacyPath);
                }
                parent.location.replace('/ui/login.action?vcf=1' + uriParam);
            }
        </script>
    </s:param>
    <s:param name="styles"></s:param>
    <s:param name="login">true</s:param>
</s:include>
<style>
    * {
        line-height: normal;
    }
    #authField .x-field {
        width: 100%;
        padding-bottom: 30px;
    }

    .login-wrapper {
        display: none !important;
    }

    .login-wrapper-visible {
        display: flex !important;
    }

    #loginForm .x-mask {
        background-color: rgba(27, 42, 50, 0.8);
    }

    #loginForm .aria-ops-login .x-mask {
        background-color: rgba(18, 28, 33, 0.8);
    }
    .spinner-center {
        margin-left: auto;
        margin-right: auto;
        left: 0;
        right: 0;
        position: absolute;
        top: 18%;
        z-index: 1000;
    }
    .login-wrapper .login {
        width: 512px !important;
        padding: 0 96px !important;
        position: relative;
        height: 100%;
    }

    .login-wrapper .aria-ops-login {
        background: #121C21 !important;
    }

    .login-wrapper .login .trademark {
        font-size: 10px;
        vertical-align: super;
    }
    .login-wrapper .login .login-group .password {
        margin: 18px 0 18px 0;
    }

    .login-wrapper .login .login-group .btn {
        margin: 24px 0 0 0;
    }
    .login-wrapper .login .login-group {
        padding: 0;
        position: relative;
        margin-bottom: 50px;
    }
    .login-wrapper {
        display: flex;
        flex-direction: row;
    }

    .copy-right {
        position: absolute;
        bottom: 18px;
        font-style: normal;
        font-weight: 400;
        font-size: 10px;
        line-height: 16px;
    }

    #li-login-container {
        min-height: 590px;
    }

    #li-login-container-right {
        height: 100vh;
        width: 100%;
        background-color: #1B2A31 !important;
        background: url(images/login_back.svg) center center no-repeat;
        min-height: 590px;
    }

    /* Change the size of the background image on smaller screens */
    @media screen and (max-width: 1200px) {
        #li-login-container-right {
            background-size: contain;
        }
    }

    /* Decreasing menu items height that appeared from clarity upgrade */
    .dropdown-menu .btn, .dropdown-menu .dropdown-item {
        height: 25px;
        line-height: inherit;
    }

</style>
<script type="text/javascript">
    Ext.onReady(function () {
        Ext.state.Manager.setProvider(new Ext.state.CookieProvider());
        var me = this;
        const vcfLoginWrapper = document.getElementById('vcf-login-wrapper');
        const ariaOpsLoginWrapper = document.getElementById('aria-ops-login-wrapper');
        sessionStorage.removeItem('vcfUIState');

        if (vcfLoginMode) {
            ariaOpsLoginWrapper.remove();
            vcfLoginWrapper.classList.add('login-wrapper-visible');
        } else {
            vcfLoginWrapper.remove();
            ariaOpsLoginWrapper.classList.add('login-wrapper-visible');
        }

        me.showAlertMessage = function (msg, type, skipEncode) {
            var errMsgIconTypesMap = {
                'warning': 'exclamation-triangle',
                'danger': 'exclamation-circle',
                'info': 'info-circle'
            };
            var msgContainer = document.getElementById('errMsgContainer');
            var errMsgWrapper = document.getElementById('errMsgWrapper');
            var errMsgIcon = document.getElementById('errMsgIcon');
            var msgText = document.getElementById('errorMsg');
            var classToApply = 'alert-' + type;
            msgContainer.style['visibility'] = 'visible';
            msgContainer.setAttribute('clralerttype', type);
            errMsgIcon.setAttribute('shape', errMsgIconTypesMap[type]);
            if (errMsgWrapper.classList.length > 1) {
                errMsgWrapper.classList.remove(errMsgWrapper.classList[1]);
            }
            errMsgWrapper.classList.add(classToApply);
            msgText.innerHTML = skipEncode ? msg : Ext.util.Format.htmlEncode(msg);
        };


        me.removeAlertMessage = function() {
            var msgContainer = document.getElementById('errMsgContainer');
            msgContainer.style['visibility'] = 'hidden';
        };

        me.showSsoErrorMessage = function (show) {
            if (!show) {
                me.removeAlertMessage();
            } else if (!Ext.isEmpty(me.ssoErrorMessage)) {
                // check whether logout link already concatenated to error message
                if (!Ext.isEmpty(me.ssoShowLogOutLink) && me.ssoErrorMessage.indexOf('onClick="logOutSSOUser()') === -1) {
                    me.ssoErrorMessage += '<br><br>';
                    me.ssoErrorMessage += '<a href="javascript:void(0)" onClick="logOutSSOUser()" style="color:#007cbb">' + '<s:property value="%{escapeQuote('sso.logout.user.link')}"/>' + '</a>';
                }
                me.showAlertMessage(me.ssoErrorMessage, 'danger', true);
            }
        };

        //Detect if SSO login failed and display error message
        this.ssoErrorMessage = '${sessionScope.ssoLoginError}';
        this.ssoLogOutURL = '${sessionScope.ssoLogOutURL}';
        this.ssoShowLogOutLink = '${sessionScope.ssoShowLogOutLink}';

        //Detect if vIDB login failed and display error message
        this.vidbLoginError = '${sessionScope.vidbLoginError}';

        //Detect if vIDM login failed and display error message
        this.vidmLoginError = '${sessionScope.vidmLoginError}';

        //Detect if vIDM or SSO login failed inside vROps
        this.loginError = '${sessionScope.loginError}';

        //Detect if should immediately direct to sso server
        this.redirectBackUrl = '${sessionScope.redirectBackUrl}';

        //Detect if should immediately direct to sso server
        this.vidbRedirectBackUrl = '${sessionScope.vidbRedirectBackUrl}';

        <% session.removeAttribute("loginError"); %>
        <% session.removeAttribute("vidmLoginError"); %>
        <% session.removeAttribute("ssoLoginError"); %>
        <% session.removeAttribute("ssoLogOutURL"); %>
        <% session.removeAttribute("ssoShowLogOutLink"); %>
        <% session.removeAttribute("redirectBackUrl"); %>
        <% session.removeAttribute("vidbRedirectBackUrl"); %>
        <% session.removeAttribute("vidbLoginError"); %>


        //Check if we need to logout of SSO session
        if (!Ext.isEmpty(me.ssoLogOutURL)) {

            if (Ext.form.field.VTypes['url'](me.ssoLogOutURL)) {
                var loginMask = Ext.get('loginMask');

                loginMask.setVisible(true);

                window.open(ssoLogOutURL, "_self");
            } else {
                me.showAlertMessage('<s:property value="%{escapeQuote('sso.logOutURL.failed.errorMsg')}"/>', 'alert-danger');
            }
        }

        var handleLoginError = function(source, loginError) {
            var errorMsg = loginError;
            if (loginError.indexOf("concurrentSessionDetected") >= 0) {
                var dialogErrorMsg = '<s:property value="%{escapeQuote('login.concurrentSessionDetected')}"/>';
                errorMsg = '<s:property value="%{escapeQuote('login.concurrentSessionError')}"/>';
                if (confirm(dialogErrorMsg)) {
                    if (source === "vIDM") {
                        this.logInVidmUser(true);
                    } else if (source === "vIDB") {
                        this.loginVIDBUser(true);
                    }
                    return;
                }
            } else if (loginError.indexOf("notAllowedInteractiveLogin") >= 0) {
                errorMsg = '<s:property value="%{escapeQuote('login.notAllowedInteractiveLogin')}"/>';
            }
            this.showAlertMessage(errorMsg, 'danger', true);
        }

        var cleanupLocalStorage = function() {
            if (Ext.supports.LocalStorage) {
                // removing widgets stored in local storage from previous session on new login
                localStorage.removeItem("copiedWidgets");
            }
        };
        //Logout of SSO when user is stuck
        this.logOutSSOUser = function () {
            window.open(me.ssoShowLogOutLink, "_self");
        };

        this.logInSSOUser = function (force) {
            var loginForm = Ext.get('loginForm');
            var loginMask = Ext.get('loginMask');
            var timezone = -new Date().getTimezoneOffset();

            loginMask.setVisible(true);

            var urlAnchor = (document.URL.split('#').length > 1) ? document.URL.split('#')[1] : '';
            loginForm.mask();
            let params_ = {
                urlAnchor: urlAnchor,
                timezone: timezone,
                forceLogin: force
            };
            if (vcfLoginMode) {
                params_.vcf = 1;
            }
            Ext.Ajax.request({
                url: 'login.action?mainAction=getSsoRedirectUrl',
                params: params_,
                disableCaching: true,
                timeout: 300000,
                scope: this,
                success: function (response) {
                    cleanupLocalStorage();
                    loginForm.unmask();
                    var responseJson = Ext.JSON.decode(response.responseText);
                    var success = responseJson.success;
                    var redirectURL = responseJson.ssoRedirectURL;
                    var errorMsg = responseJson.errorMsg;

                    if (success === true) {

                        if (Ext.isEmpty(redirectURL)) {
                            me.showAlertMessage('<s:property value="%{escapeQuote('sso.rederictURL.null.errorMsg')}"/>', 'danger');
                        } else if (!Ext.form.field.VTypes['url'](redirectURL)) {
                            me.showAlertMessage('<s:property value="%{escapeQuote('sso.rederictURL.invalid.errorMsg')}"/>', 'danger')
                        } else {
                            window.open(redirectURL, "_self");
                        }

                    } else {
                        if (Ext.isEmpty(errorMsg)) {
                            errorMsg = '<s:property value="%{escapeQuote('sso.rederictURL.failed.errorMsg')}"/>';
                        }
                        me.showAlertMessage(errorMsg, 'danger');
                    }
                },
                failure: function () {
                    me.showAlertMessage('<s:property value="%{escapeQuote('sso.rederictURL.failed.errorMsg')}"/>', 'danger');
                    loginMask.setDisplayed(false);
                    loginForm.unmask();
                }
            });

        };

        this.loginVIDBUser = function (force) {
            var loginForm = Ext.get('loginForm');
            var loginMask = Ext.get('loginMask');
            var timezone = -new Date().getTimezoneOffset();
            var browserHost = window.location.host;

            loginMask.setVisible(true);

            var urlAnchor = (document.URL.split('#').length > 1) ? document.URL.split('#')[1] : '';
            loginForm.mask();
            let params_ = {
                urlAnchor: urlAnchor,
                timezone: timezone,
                browserHost: browserHost,
                forceLogin: force
            };
            if (vcfLoginMode) {
                params_.vcf = 1;
            }
            Ext.Ajax.request({
                url: 'login.action?mainAction=getVIDBRedirectUrl',
                params: params_,
                disableCaching: true,
                timeout: 300000,
                scope: this,
                success: function (response) {
                    cleanupLocalStorage();
                    loginForm.unmask();
                    var responseJson = Ext.JSON.decode(response.responseText);
                    var success = responseJson.success;
                    var redirectURL = responseJson.vidbRedirectURL;
                    var errorMsg = responseJson.errorMsg;

                    if (success === true) {
                        if (Ext.isEmpty(redirectURL)) {
                            me.showAlertMessage('<s:property value="%{escapeQuote('login.vidb.redirectURL.null.errorMsg')}"/>', 'danger');
                        } else if (!Ext.form.field.VTypes['url'](redirectURL)) {
                            me.showAlertMessage('<s:property value="%{escapeQuote('login.vidb.redirectURL.invalid.errorMsg')}"/>', 'danger')
                        } else {
                            window.open(redirectURL, "_self");
                        }

                    } else {
                        if (Ext.isEmpty(errorMsg)) {
                            errorMsg = '<s:property value="%{escapeQuote('login.vidb.redirectURL.failed.errorMsg')}"/>';
                        }
                        me.showAlertMessage(errorMsg, 'danger');
                    }
                },
                failure: function () {
                    me.showAlertMessage('<s:property value="%{escapeQuote('sso.rederictURL.failed.errorMsg')}"/>', 'danger');
                    loginMask.setDisplayed(false);
                    loginForm.unmask();
                }
            });

        };

        this.logInVidmUser = function (force) {
            var loginMask = Ext.get('loginMask');
            var timezone = -new Date().getTimezoneOffset();

            loginMask.setVisible(true);

            var urlAnchor = (document.URL.split('#').length > 1) ? document.URL.split('#')[1] : '';

            let params_ = {
                urlAnchor: urlAnchor,
                timezone: timezone,
                forceLogin: force
            }
            if (vcfLoginMode) {
                params_.vcf = 1;
            }

            Ext.Ajax.request({
                url: 'login.action?mainAction=getVidmRedirectUrl',
                params: params_,
                disableCaching: true,
                timeout: 300000,
                scope: this,
                success: function(response) {
                    cleanupLocalStorage();
                    var responseJson = Ext.JSON.decode(response.responseText);
                    var success = responseJson.success;
                    var redirectURL = responseJson.vidmRedirectURL;
                    var errorMsg = responseJson.errorMsg;

                    if(success === true) {

                        if(Ext.isEmpty(redirectURL)) {
                            this.showAlertMessage('<s:property value="%{escapeQuote('login.vidm.rederictURL.null.errorMsg')}"/>', 'info');
                        }else if(!Ext.form.field.VTypes['url'](redirectURL)) {
                            this.showAlertMessage('<s:property value="%{escapeQuote('login.vidm.rederictURL.invalid.errorMsg')}"/>', 'info');
                        } else {
                            window.open(redirectURL, "_self");
                        }

                    } else {
                        if(Ext.isEmpty(errorMsg)){ errorMsg = '<s:property value="%{escapeQuote('login.vidm.rederictURL.failed.errorMsg')}"/>';}
                        this.showAlertMessage(errorMsg, 'alert-danger');
                    }
                },
                failure: function()  {
                    me.showAlertMessage('<s:property value="%{escapeQuote('login.vidm.rederictURL.failed.errorMsg')}"/>', 'danger');
                    loginMask.setDisplayed(false);
                }
            });

        };

        //Check if we need to logout of SSO session
        if (!Ext.isEmpty(me.redirectBackUrl)) {
            this.logInSSOUser();
        }

        //Check if we need to logout of SSO session
        if (!Ext.isEmpty(me.vidbRedirectBackUrl)) {
            this.loginVIDBUser();
        }

        this.authStore = Ext.create('Ext.data.Store', {
            fields: ['id', 'name', 'description', 'type'],
            proxy: {
                type: 'ajax',
                url: 'login.action?mainAction=getAuthSources',
                reader: {
                    type: 'json'
                }
            },
            autoLoad: window.location.search.endsWith('previewLoginMessage') ? false : true,
            listeners: {
                scope: this,
                'load': function (store, records) {
                    //Retrieve the state of AuthSource combobox by passing it's stateId
                    var authSourceState = Ext.state.Manager.get('login-authSourceSelector');
                    var sourceIndex = -1;
                    if (authSourceState && authSourceState.value) {
                        //Ensure that the AuthSource that was used for login, still exists in getAuthSources() response
                        sourceIndex = store.findBy(
                            function (record) {
                                if (record.get('id') === authSourceState.value) {
                                    return true;  // an AuthSource with the stored state exists
                                }
                                return false;  // there is no AuthSource in the store with this data
                            }
                        );
                    }

                    // If the source does not exist (might have been deleted by other users meanwhile), do not try
                    // to retain the combobox state. Show 'Local Users' then.
                    this.authSrcField.setValue(sourceIndex == -1 ? '${localItem}' : records[sourceIndex]);

                    if (!Ext.isEmpty(me.ssoErrorMessage)) {
                        if (ssoErrorMessage.indexOf("concurrentSessionDetected") >= 0) {
                            var dialogErrorMsg = '<s:property value="%{escapeQuote('login.concurrentSessionDetected')}"/>';
                            var errorMsg = '<s:property value="%{escapeQuote('login.concurrentSessionError')}"/>';
                            if (confirm(dialogErrorMsg)) {
                                this.logInSSOUser(true);
                                return;
                            } else {
                                this.showAlertMessage(errorMsg, 'danger');
                            }
                        }
                        var ssoSourceId;
                        var ssoSource = records.filter(function(record) {
                            return record.get('type') === 'SSO_SAML';
                        });
                        if (ssoSource[0].internalId -1 == sourceIndex) {
                            me.showSsoErrorMessage(true);
                        }
                    }

                    if (window.localStorage && (!window.frameElement || window.frameElement.id != 'd')) {
                        window.localStorage.removeItem('login');
                        window.localStorage.setItem('login', 'loaded');
                    }

                    if (!Ext.isEmpty(vidmLoginError)) {
                        handleLoginError("vIDM", vidmLoginError);
                    }

                    if (!Ext.isEmpty(vidbLoginError)) {
                        handleLoginError("vIDB", vidbLoginError);
                    }

                    if (!Ext.isEmpty(loginError)) {
                        var errorMsg = loginError;
                        if (loginError.indexOf("notAllowedInteractiveLogin") >= 0) {
                            errorMsg = '<s:property value="%{escapeQuote('login.notAllowedInteractiveLogin')}"/>';
                        }
                        this.showAlertMessage(errorMsg, 'danger');
                    }
                }
            }
        });

        // Auth source selector
        this.authSrcField = Ext.create('Ext.form.ComboBox', {
            id: 'authSelector',
            store: this.authStore,
            queryMode: 'local',
            editable: false,
            displayField: 'name',
            valueField: 'id',
            renderTo: 'authField',
            value: '${localItem}',
            stateful: true,
            stateId: 'login-authSourceSelector',
            hideLabel: true,
            fieldLabel: "${bundle.getProperty("login.authsource")}",
            listeners: {
                beforestaterestore: function (cmp, state) {
                    // Since the "LOCAL" option doesn't have a value (it's null/undefined) we need
                    // to return if we find an empty value so that the combobox doesn't get confused
                    // and leave the field blank. Stopping the
                    if (!state.value) {
                        return false;
                    }
                },
                change: function (combo, newVal, oldVal) {
                    var selected = combo.getSelectedRecord();
                    var sourceType;

                    if (selected != null) {
                        sourceType = selected.get('type');
                    }

                    //var sourceType = combo.getSelectedRecord().get('type');
                    var loginBtn = Ext.getCmp("loginBtn");
                    if (loginBtn.hasCls('disabled')) {
                        loginBtn.removeCls('disabled');
                        loginBtn.enable();
                    }
                    if (sourceType !== 'SSO_SAML' && me.ssoSelected) {
                        this.showSsoErrorMessage(false);
                        me.ssoSelected = false;
                    }

                    if (sourceType == 'SSO_SAML') {
                        // disable the text fields
                        userNameField.disable();
                        passwordField.disable();
                        //TODO: tooltip not appearing for some reason

                        loginBtn.setText('<s:property value="%{escapeQuote('sso.button.redirect')}"/>');
                        loginBtn.setTooltip('<s:property value="%{escapeQuote('sso.button.redirect.tooltip')}"/>');

                        if (!Ext.isEmpty(me.ssoErrorMessage) && !Ext.isEmpty(me.ssoShowLogOutLink)) {
                            loginBtn.addCls('disabled');
                            loginBtn.disable();
                        }
                        // temprerary flag to fix bug #2139639
                        this.ssoSelected = true;

                        me.showSsoErrorMessage(true);
                    } else if (sourceType == 'VIDM') {
                        // disable the text fields
                        userNameField.disable();
                        passwordField.disable();
                        //TODO: tooltip not appearing for some reason
                        loginBtn.setText('<s:property value="%{escapeQuote('login.vidm.redirectBtn')}"/>');
                        loginBtn.setTooltip('<s:property value="%{escapeQuote('login.vidm.redirectBtn.tooltip')}"/>');
                    } else if (sourceType == 'VIDB') {
                        userNameField.disable();
                        passwordField.disable();
                        loginBtn.setText('<s:property value="%{escapeQuote('login.vidb.redirectBtn')}"/>');
                        loginBtn.setTooltip('<s:property value="%{escapeQuote('login.vidb.redirectBtn.tooltip')}"/>');
                    }  else {
                        userNameField.enable();
                        passwordField.enable();
                        loginBtn.setText('<s:property value="%{escapeQuote('login.login')}"/>');
                        loginBtn.setTooltip('<s:property value="%{escapeQuote('login.login')}"/>');
                    }
                },
                scope: this
            },
            listConfig: {
                tpl: Ext.create('Ext.XTemplate',
                    '<ul><tpl for=".">',
                    '<li role="option" class="x-boundlist-item" title="{[Ext.String.htmlEncode(this.getToolTip(values))]}">{[this.getEncodedSource(values.name, values.type)]}</li>',
                    '</tpl>' +
                    '</ul>',
                    {
                        getToolTip: function (values) {
                            if (values.type) {

                                var toolTipTemplate = '${bundle["authSourceType.toolTip.template"]}';

                                switch (values.type) {
                                    case 'VC':
                                        return Ext.String.format(toolTipTemplate, values.name, '<s:property value="%{escapeQuote('authSourceType.VC')}"/>');
                                        break;

                                    case 'OPEN_LDAP':
                                        return Ext.String.format(toolTipTemplate, values.name, '<s:property value="%{escapeQuote('authSourceType.OPEN_LDAP')}"/>');
                                        break;

                                    case 'VC_GROUP':
                                        return Ext.String.format(toolTipTemplate, values.name, '<s:property value="%{escapeQuote('authSourceType.VC_GROUP')}"/>');
                                        break;

                                    case 'ACTIVE_DIRECTORY':
                                        return Ext.String.format(toolTipTemplate, values.name, '<s:property value="%{escapeQuote('authSourceType.ACTIVE_DIRECTORY')}"/>');
                                        break;

                                    case 'OTHER':
                                        return Ext.String.format(toolTipTemplate, values.name, '<s:property value="%{escapeQuote('authSourceType.OTHER')}"/>');
                                        break;

                                    case 'LOCAL':
                                        return Ext.String.format(toolTipTemplate, values.name, '<s:property value="%{escapeQuote('authSourceType.LOCAL')}"/>');
                                        break;

                                    case 'SSO_SAML':
                                        return Ext.String.format(toolTipTemplate, values.name, '<s:property value="%{escapeQuote('authSourceType.SSO_SAML')}"/>');
                                        break;

                                    default:
                                        return values.name;
                                }

                            } else {
                                return values.name;
                            }
                        },
                        getEncodedSource: function (name, type) {
                            // For differentiating SSO hosts, add a suffix label
                            var suffix = (type == 'SSO_SAML') ? ' ' + '${bundle["sso.label2"]}' : '';

                            // Encode the sourceName to avoid XSS attacks
                            return Ext.String.htmlEncode(name) + suffix;
                        }
                    }
                )
            }
        });

        var userNameField = new Ext.form.TextField({
            id: 'userName',
            renderTo: 'userNameField',
            width: '100%',
            emptyText: "${bundle.getProperty("login.userName")}"
        });

        var login = function (force) {
            var userName = userNameField.getValue();
            var password = passwordField.getValue();
            var authSourceId = authSrcField.getValue();
            var authSourceName = authSrcField.rawValue;

            var authSourceType = "LOCAL";
            var source = this.authStore.getById(authSourceId);
            if (source && !Ext.isEmpty(source.data.type)) {
                authSourceType = source.data.type;
            }

            if (authSourceType == 'SSO_SAML') {
                this.logInSSOUser();
                return;
            }

            if (authSourceType == 'VIDM') {
                this.logInVidmUser();
                return;
            }

            if (authSourceType == 'VIDB') {
                this.loginVIDBUser();
                return;
            }

            if (userName != '' && password != '') {
                var loginForm = Ext.get('loginForm');
                var loginMask = Ext.get('loginMask');

                loginMask.setVisible(true);
                loginForm.mask();
                let params_ = {
                    mainAction: 'login',
                    userName: userName,
                    password: password,
                    authSourceId: authSourceId,
                    authSourceName: authSourceName,
                    authSourceType: authSourceType,
                    forceLogin: force,
                    timezone: (-new Date().getTimezoneOffset()),
                    languageCode: 'us'
                };
                if (vcfLoginMode) {
                    params_.vcf = 1;
                }
                Ext.Ajax.request({
                    url: 'login.action',
                    method: 'POST',
                    params: Ext.urlEncode(params_),
                    disableCaching: true,
                    timeout: 900000,
                    success: function (response, options) {
                        cleanupLocalStorage();
                        var responseText = response.responseText;
                        if (responseText.indexOf('<!-- SessionResolve_Page -->') > 0) {
                            window.location.reload(true);
                            return;
                        }
                        if (responseText == 'ok') {
                            window.location.reload();
                        } else if (responseText.indexOf('<!-- Index_Page -->') > 0) {
                            // Bug 1477494 - Corner case scenario where the user opens up login pages in two separate tabs,
                            // From first tab - user logs in successfully ,
                            // From second tab - if he tries to login again (without refreshing the browser), then he should
                            //    be redirected to our main page (instead of "incorrect login" error shown in login form ).
                            //    If user chooses to refresh the second tab instead, the user will automatically be
                            //    redirected to the main page. The same functionality is expected even when the user clicks 'login'
                            window.location.reload();
                        } else {
                            var errorMsg = '<s:property value="%{escapeQuote('login.incorrectLogin')}"/>';
                            if (responseText.toLowerCase().indexOf("locked") >= 0) {
                                //Locked account should appear as incorrect login per PSP policy
                                errorMsg = '<s:property value="%{escapeQuote('login.incorrectLogin')}"/>';
                            } else if (responseText.toLowerCase().indexOf("role") >= 0) {
                                //TODO display localized message from backend API response
                                errorMsg = '<s:property value="%{escapeQuote('login.noRolesErrorMessage')}"/>';
                            } else if (responseText.indexOf("Unable to connect to any locators") >= 0 || responseText.indexOf("rejected from java.util.concurrent.ThreadPoolExecutor") >= 0) {
                                errorMsg = '<s:property value="%{escapeQuote('login.unableToConnectPlatform')}"/>';
                            } else if (responseText.indexOf("controllerDown") >= 0) {
                                errorMsg = '<s:property value="%{escapeQuote('login.controllerDown')}"/>';
                            } else if (responseText.indexOf("casaIsDown") >= 0) {
                                errorMsg = '<s:property value="%{escapeQuote('login.casaIsDown')}"/>';
                            } else if (responseText.indexOf("notAllowedInteractiveLogin") >= 0) {
                                errorMsg = '<s:property value="%{escapeQuote('login.notAllowedInteractiveLogin')}"/>';
                            } else if (responseText.indexOf("SourceUnavailable") >= 0) {
                                errorMsg = '<s:property value="%{escapeQuote('login.hostUnreachable')}"/>';
                            } else if (responseText.indexOf("MultipleDomainsExist") >= 0) {
                                errorMsg = '<s:property value="%{escapeQuote('login.multipleDomainsExist')}"/>';
                            } else if (responseText.indexOf("concurrentSessionDetected") >= 0) {
                                errorMsg = '<s:property value="%{escapeQuote('login.concurrentSessionDetected')}"/>';
                                var r = confirm(errorMsg);
                                if (r == true) {
                                    login(true);
                                    return;
                                } else {
                                    errorMsg = '<s:property value="%{escapeQuote('login.concurrentSessionError')}"/>';
                                }
                            }

                            loginMask.setDisplayed(false);
                            loginForm.unmask();

                            me.showAlertMessage(errorMsg, 'danger');
                        }
                    },
                    failure: function (response, options) {
                        loginMask.setDisplayed(false);
                        loginForm.unmask();
                    }
                });
            }
            else {
                me.showAlertMessage('<s:property value="%{escapeQuote('login.enterUserNameAndPassword')}"/>', 'danger');
            }
        };

        document.body.onkeyup = function (e) {
            if (e.keyCode == Ext.event.Event.CAPS_LOCK) {
                me.removeAlertMessage();
            }
        };

        var passwordField = new Ext.form.TextField ({
            id: 'password',
            renderTo: 'passwordField',
            inputType: 'password',
            emptyText: "${bundle.getProperty("login.password")}",
            scope: this,
            width: '100%',
            enableKeyEvents: true,
            listeners: {
                keydown: function (field, e) {
                    var event = e.browserEvent;
                    if ((event.which && event.which == 13) || (event.keyCode && event.keyCode == 13)) {
                        login();
                    }
                },
                keypress: function (field, e) {
                    var charCode = e.getCharCode();
                    if (!e.shiftKey && charCode >= Ext.event.Event.A && charCode <= Ext.event.Event.Z) {
                        me.showAlertMessage('<s:property value="%{escapeQuote('common.capsLock.warning')}"/>', 'warning');
                    }
                },
                blur: function () {
                    me.removeAlertMessage();
                }
            }
        });

        var loginBtn = new Ext.Button({
            id: 'loginBtn',
            renderTo: 'loginForm',
            cls: 'btn btn-primary btn-block',
            ui: '',
            text: '${bundle["login.login"]}',
            scope: this,
            handler: login
        });

        //Login Consent Dialog
        var showLoginMessageDialog = function(title, content, buttonLbl) {

            var loginConsentDialog = new Ext.window.Window({
                modal: true,
                resizable: true,
                closable: false,
                minWidth: 350,
                minHeight: 260,
                layout: {
                    type: 'fit',
                    align: 'stretch'
                },
                items: [{
                    xtype: 'component',
                    id: 'dialogLoginConsent',
                    padding: '20 20 20 20',
                    html: '<style type="text/css">div.reset-css-style li {list-style-type: unset;list-style: unset;} ' +
                        'div.reset-css-style ol, div.reset-css-style ul { padding-inline-start: 20px; padding-bottom: 10px; } </style>' +
                        '<div class="reset-css-style" style="text-align: justify; max-height: 450px; max-width: 700px; overflow:auto">' +
                        DOMPurify.sanitize(content) + '</div>'
                }],
                title: Ext.htmlEncode(title),
                buttons: [{
                    xtype: 'component',
                    html: '<button class="btn btn-primary">' +
                        Ext.htmlEncode(buttonLbl) + '</button>',
                    listeners: {
                        click: {
                            element: 'el',
                            fn: function () {
                                if (!window.location.search.endsWith('previewLoginMessage')) {
                                    loginConsentDialog.close();
                                }
                            }
                        },
                        scope: this
                    }
                }]
            });
            loginConsentDialog.show();
        }
        var loadMessageSettings = function() {
            Ext.Ajax.request({
                url: 'login.action',
                params: {mainAction: 'getLoginMessage'},
                method: 'POST',
                success: function (response) {
                    var record = Ext.JSON.decode(response.responseText);
                    if (record.displayOnLogin) {
                        showLoginMessageDialog(record.title, record.content, record.buttonLabel);
                    }
                }
            });
        }
        if (window.location.search.endsWith('previewLoginMessage')) {
            var previewTitle = window.localStorage.getItem('previewTitle');
            var previewContent = window.localStorage.getItem('previewContent');
            var previewButtonLabel = window.localStorage.getItem('previewButtonLabel');
            document.getElementById('previewInfo').style.display = 'block';
            showLoginMessageDialog(previewTitle, previewContent, previewButtonLabel);
        } else {
            loadMessageSettings();
        }
        userNameField.focus();

        <s:if test="${dataRetrieverInitialized != true}">
        Ext.get('loginMask').setVisible(true);
        me.showAlertMessage('<s:property value="%{escapeQuote('login.dataRetrieverNotInitialized')}"/>', 'info');
        authSrcField.disable();
        userNameField.disable();
        passwordField.disable();
        loginBtn.addCls('disabled');
        loginBtn.disable();
        setTimeout('window.location.reload(true);', 10000);
        </s:if>

        <s:if test="${controllerDown == true}">
        this.showAlertMessage('<s:property value="%{escapeQuote('login.controllerDown')}"/>', 'info');
        </s:if>
    });
</script>
</head>
<body>
<div id="previewInfo" class="alert alert-app-level alert-info" style="z-index:99999;display:none;" role="info">
    <div class="alert-items">
        <div class="alert-item static">
            <div class="alert-icon-wrapper">
                <clr-icon class="alert-icon" shape="info-circle"></clr-icon>
            </div>
            <div class="alert-text" style="flex-basis:0%;white-space:nowrap;">
                ${fn:escapeXml(bundle["login.previewmode.info"])}
            </div>
            <button class="btn alert-action" onclick="window.close();">${fn:escapeXml(bundle["login.previewmode.close"])}</button>
        </div>
    </div>
</div>
<div class="login-wrapper" id="aria-ops-login-wrapper">
    <div id="li-login-container">
        <form class="login aria-ops-login">
            <div style="height: 80px; width: 100%; background: url(images/VM-Logo.svg) no-repeat; margin-left: -50px; margin-bottom: 42px;">&nbsp;</div>
            <div style="font-weight: 400; width: 100%; font-size: 18px; padding-bottom: 24px;">${bundle.getProperty("login.welcome")}</div>
            <div class="title">
                VMware Aria
            </div>
            <div style="padding-bottom: 42px;" class="title">
                Operations<span class="trademark">&trade;</span>
            </div>
            <div id="loginForm" class="login-group">
                <div id="authField"></div>
                <div class="username" id="userNameField"></div>
                <div class="password" id="passwordField"></div>
                <clr-alert style="visibility:hidden" id="errMsgContainer" >
                    <div id="errMsgWrapper" class="alert ">
                        <div class="alert-items">
                            <clr-alert-item class="alert-item">
                                <div class="alert-icon-wrapper">
                                    <clr-icon id="errMsgIcon" class="alert-icon" shape="danger"></clr-icon>
                                </div>
                                <span id="errorMsg" class="alert-text"></span>
                            </clr-alert-item>
                        </div>
                    </div>
                </clr-alert>
                <div id="loginMask" style="display:none" class="spinner spinner-center"></div>
            </div>
            <div id="copy-right" class="copy-right">${bundle.getProperty("login.copyright")}</div>
        </form>
    </div>
    <div id="li-login-container-right"></div>
</div>

<div class="login-wrapper" id="vcf-login-wrapper" style="background: url(images/vropsninja.png);background-repeat: no-repeat; background-size: cover; background-position: center; width: 100vw; height: 100vh;">
    <form class="login" style="background: none;">
        <div style="width: 100%">
            <img src="images/VMware_by_Broadcom_White_Logo.svg" style="width: 170px; margin-bottom: 62px;"/>
        </div>
        <div style="font-weight: 400; width: 100%; font-size: 18px; padding-bottom: 24px;">${bundle.getProperty("login.welcome")}</div>
        <div class="title">
            VMware Aria
        </div>
        <div style="padding-bottom: 42px;" class="title">
            Operations<span class="trademark">&trade;</span>
        </div>
        <div id="loginForm" class="login-group">
            <div id="authField"></div>
            <div class="username" id="userNameField"></div>
            <div class="password" id="passwordField"></div>
            <clr-alert style="visibility:hidden" id="errMsgContainer" >
                <div id="errMsgWrapper" class="alert ">
                    <div class="alert-items">
                        <clr-alert-item class="alert-item">
                            <div class="alert-icon-wrapper">
                                <clr-icon id="errMsgIcon" class="alert-icon" shape="danger"></clr-icon>
                            </div>
                            <span id="errorMsg" class="alert-text"></span>
                        </clr-alert-item>
                    </div>
                </div>
            </clr-alert>
            <div id="loginMask" style="display:none" class="spinner spinner-center"></div>
        </div>
        <div id="copy-right" class="copy-right">${bundle.getProperty("login.copyright")}</div>
    </form>
</div>

</div>
<!-- This comment is necessary for AJAX handlers (see commonJS.jsp). Don't modify or remove. -->
<!-- Login_Page -->
</body>
</html>
