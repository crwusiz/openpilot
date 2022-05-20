#include "selfdrive/ui/qt/widgets/ssh_keys.h"

#include "common/params.h"
#include "selfdrive/ui/qt/api.h"
#include "selfdrive/ui/qt/widgets/input.h"

//SshControl::SshControl() : ButtonControl("SSH Keys", "", "Warning: This grants SSH access to all public keys in your GitHub settings. Never enter a GitHub username other than your own. A comma employee will NEVER ask you to add their GitHub username.") {
SshControl::SshControl() : ButtonControl("SSH Keys", "", "Github 사용자 ID에 등록된 SSH키로 변경합니다.") {
  username_label.setAlignment(Qt::AlignRight | Qt::AlignVCenter);
  username_label.setStyleSheet("color: #e0e879");
  hlayout->insertWidget(1, &username_label);

  QObject::connect(this, &ButtonControl::clicked, [=]() {
    //if (text() == "ADD") {
      //QString username = InputDialog::getText("Enter your GitHub username", this);
    if (text() == "개인키사용") {
      QString username = InputDialog::getText("GitHub ID를 입력하세요", this);
      if (username.length() > 0) {
        //setText("LOADING");
        setText("로딩중");
        setEnabled(false);
        getUserKeys(username);
      }
    } else {
      params.remove("GithubUsername");
      params.remove("GithubSshKeys");
      refresh();
    }
  });

  refresh();
}

void SshControl::refresh() {
  QString param = QString::fromStdString(params.get("GithubSshKeys"));
  if (param.length()) {
    username_label.setText(QString::fromStdString(params.get("GithubUsername")));
    //setText("REMOVE");
    setText("개인키제거");
  } else {
    username_label.setText("");
    //setText("ADD");
    setText("개인키사용");
  }
  setEnabled(true);
}

void SshControl::getUserKeys(const QString &username) {
  HttpRequest *request = new HttpRequest(this, false);
  QObject::connect(request, &HttpRequest::requestDone, [=](const QString &resp, bool success) {
    if (success) {
      if (!resp.isEmpty()) {
        params.put("GithubUsername", username.toStdString());
        params.put("GithubSshKeys", resp.toStdString());
      } else {
        //ConfirmationDialog::alert(QString("Username '%1' has no keys on GitHub").arg(username), this);
        ConfirmationDialog::alert(username + "등록된 SSH키가 없습니다.", this);
      }
    } else {
      if (request->timeout()) {
        //ConfirmationDialog::alert("Request timed out", this);
        ConfirmationDialog::alert("요청시간이 초과되었습니다.", this);
      } else {
        //ConfirmationDialog::alert(QString("Username '%1' doesn't exist on GitHub").arg(username), this);
        ConfirmationDialog::alert(username + "등록된 사용자가 아닙니다.", this);
      }
    }

    refresh();
    request->deleteLater();
  });

  request->sendRequest("https://github.com/" + username + ".keys");
}

//LateralControlSelect
LateralControlSelect::LateralControlSelect() : AbstractControl("LateralControl [√]", "조향로직을 선택합니다. (Pid/Indi/Lqr/Torque)", "../assets/offroad/icon_logic.png") {
  label.setAlignment(Qt::AlignVCenter|Qt::AlignRight);
  label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&label);

  btnminus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnplus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnminus.setFixedSize(120, 100);
  btnplus.setFixedSize(120, 100);

  hlayout->addWidget(&btnminus);
  hlayout->addWidget(&btnplus);

  QObject::connect(&btnminus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LateralControlSelect"));
    int latcontrol = str.toInt();
    latcontrol = latcontrol - 1;
    if (latcontrol <= 0 ) {
      latcontrol = 0;
    }
    QString latcontrols = QString::number(latcontrol);
    Params().put("LateralControlSelect", latcontrols.toStdString());
    refresh();
  });

  QObject::connect(&btnplus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LateralControlSelect"));
    int latcontrol = str.toInt();
    latcontrol = latcontrol + 1;
    if (latcontrol >= 3 ) {
      latcontrol = 3;
    }
    QString latcontrols = QString::number(latcontrol);
    Params().put("LateralControlSelect", latcontrols.toStdString());
    refresh();
  });
  refresh();
}

void LateralControlSelect::refresh() {
  QString latcontrol = QString::fromStdString(Params().get("LateralControlSelect"));
  if (latcontrol == "0") {
    label.setText(QString::fromStdString("Pid"));
  } else if (latcontrol == "1") {
    label.setText(QString::fromStdString("Indi"));
  } else if (latcontrol == "2") {
    label.setText(QString::fromStdString("Lqr"));
  } else if (latcontrol == "3") {
    label.setText(QString::fromStdString("Torque"));
  }
  btnminus.setText("◀");
  btnplus.setText("▶");
}

//MfcSelect
MfcSelect::MfcSelect() : AbstractControl("MFC [√]", "MFC를 선택합니다. (Lkas/Ldws/Lfa)", "../assets/offroad/icon_mfc.png") {
  label.setAlignment(Qt::AlignVCenter|Qt::AlignRight);
  label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&label);

  btnminus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnplus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnminus.setFixedSize(120, 100);
  btnplus.setFixedSize(120, 100);

  hlayout->addWidget(&btnminus);
  hlayout->addWidget(&btnplus);

  QObject::connect(&btnminus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("MfcSelect"));
    int mfc = str.toInt();
    mfc = mfc - 1;
    if (mfc <= 0 ) {
      mfc = 0;
    }
    QString mfcs = QString::number(mfc);
    Params().put("MfcSelect", mfcs.toStdString());
    refresh();
  });

  QObject::connect(&btnplus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("MfcSelect"));
    int mfc = str.toInt();
    mfc = mfc + 1;
    if (mfc >= 2 ) {
      mfc = 2;
    }
    QString mfcs = QString::number(mfc);
    Params().put("MfcSelect", mfcs.toStdString());
    refresh();
  });
  refresh();
}

void MfcSelect::refresh() {
  QString mfc = QString::fromStdString(Params().get("MfcSelect"));
  if (mfc == "0") {
    label.setText(QString::fromStdString("Lkas"));
  } else if (mfc == "1") {
    label.setText(QString::fromStdString("Ldws"));
  } else if (mfc == "2") {
    label.setText(QString::fromStdString("Lfa"));
  }
  btnminus.setText("◀");
  btnplus.setText("▶");
}

//AebSelect
AebSelect::AebSelect() : AbstractControl("AEB [√]", "AEB 신호를 선택합니다. (Scc12/Fca11)", "../assets/offroad/icon_aeb.png") {
  label.setAlignment(Qt::AlignVCenter|Qt::AlignRight);
  label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&label);

  btnminus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnplus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnminus.setFixedSize(120, 100);
  btnplus.setFixedSize(120, 100);

  hlayout->addWidget(&btnminus);
  hlayout->addWidget(&btnplus);

  QObject::connect(&btnminus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("AebSelect"));
    int aeb = str.toInt();
    aeb = aeb - 1;
    if (aeb <= 0 ) {
      aeb = 0;
    }
    QString aebs = QString::number(aeb);
    Params().put("AebSelect", aebs.toStdString());
    refresh();
  });

  QObject::connect(&btnplus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("AebSelect"));
    int aeb = str.toInt();
    aeb = aeb + 1;
    if (aeb >= 1 ) {
      aeb = 1;
    }
    QString aebs = QString::number(aeb);
    Params().put("AebSelect", aebs.toStdString());
    refresh();
  });
  refresh();
}

void AebSelect::refresh() {
  QString aeb = QString::fromStdString(Params().get("AebSelect"));
  if (aeb == "0") {
    label.setText(QString::fromStdString("Scc12"));
  } else if (aeb == "1") {
    label.setText(QString::fromStdString("Fca11"));
  }
  btnminus.setText("◀");
  btnplus.setText("▶");
}

//LongControlSelect
LongControlSelect::LongControlSelect() : AbstractControl("LongControl [√]", "LongControl 모드를 선택합니다. (Mad/Mad+Long)", "../assets/offroad/icon_long.png") {
  label.setAlignment(Qt::AlignVCenter|Qt::AlignRight);
  label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&label);

  btnminus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnplus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnminus.setFixedSize(120, 100);
  btnplus.setFixedSize(120, 100);

  hlayout->addWidget(&btnminus);
  hlayout->addWidget(&btnplus);

  QObject::connect(&btnminus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LongControlSelect"));
    int longcontrol = str.toInt();
    longcontrol = longcontrol - 1;
    if (longcontrol <= 0 ) {
      longcontrol = 0;
    }
    QString longcontrols = QString::number(longcontrol);
    Params().put("LongControlSelect", longcontrols.toStdString());
    refresh();
  });

  QObject::connect(&btnplus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LongControlSelect"));
    int longcontrol = str.toInt();
    longcontrol = longcontrol + 1;
    if (longcontrol >= 1 ) {
      longcontrol = 1;
    }
    QString longcontrols = QString::number(longcontrol);
    Params().put("LongControlSelect", longcontrols.toStdString());
    refresh();
  });
  refresh();
}

void LongControlSelect::refresh() {
  QString longcontrol = QString::fromStdString(Params().get("LongControlSelect"));
  if (longcontrol == "0") {
    label.setText(QString::fromStdString("Mad"));
  } else if (longcontrol == "1") {
    label.setText(QString::fromStdString("Mad+Long"));
  }
  btnminus.setText("◀");
  btnplus.setText("▶");
}
